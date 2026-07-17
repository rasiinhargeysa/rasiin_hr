# Copyright (c) 2023, Rasiin and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate, format_duration
from datetime import timedelta, time as dt_time
from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
from erpnext.setup.doctype.holiday_list.holiday_list import is_holiday


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	report_summary = get_report_summary(data)
	return columns, data, None, None, report_summary


# ---------------------------------------------------------------------------
# Helpers (shared logic, mirrors Employee Attendance Report)
# ---------------------------------------------------------------------------

def _to_timedelta(value):
	"""Convert a Time/str/timedelta value to a timedelta for comparison."""
	if value is None:
		return None
	if isinstance(value, timedelta):
		return value
	if isinstance(value, dt_time):
		return timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)
	value = str(value)
	if " " in value:
		value = value.split(" ")[1]
	parts = [int(p) for p in value.split(":")]
	while len(parts) < 3:
		parts.append(0)
	h, m, s = parts[:3]
	return timedelta(hours=h, minutes=m, seconds=s)


def get_employee_slots(employee):
	"""
	Returns the employee's configured Shift Type Slot rows
	(child table on Employee), sorted by time_in.

	Per-shift settings are read from the Shift Type doctype:
	  - two_date_shift: if checked, time_out is offset by +24h
	    for linear comparisons (overnight shift).
	  - begin_check_in_before_shift_start_time (minutes): how early
	    before shift start a check-in is still accepted.
	  - allow_check_out_after_shift_end_time (minutes): how late
	    after shift end a check-out is still accepted.
	"""
	emp_doc = frappe.get_cached_doc("Employee", employee)
	slots = []
	for row in getattr(emp_doc, "shift_type_slot", []) or []:
		if not row.time_in or not row.time_out:
			continue

		time_in = _to_timedelta(row.time_in)
		time_out = _to_timedelta(row.time_out)

		is_two_date = False
		begin_check_in_before = timedelta(0)
		allow_check_out_after = timedelta(0)

		if row.shift_type:
			shift_doc = frappe.db.get_value(
				"Shift Type",
				row.shift_type,
				[
					"two_date_shift",
					"begin_check_in_before_shift_start_time",
					"allow_check_out_after_shift_end_time",
				],
				as_dict=True,
			)
			if shift_doc:
				is_two_date = bool(shift_doc.get("two_date_shift"))
				begin_check_in_before = timedelta(
					minutes=shift_doc.get("begin_check_in_before_shift_start_time") or 0
				)
				allow_check_out_after = timedelta(
					minutes=shift_doc.get("allow_check_out_after_shift_end_time") or 0
				)

		# Raw (un-offset) duration of this slot, for expected-hours calc
		raw_duration = time_out - time_in
		if raw_duration < timedelta(0):
			# overnight slot, e.g. 22:00 -> 07:00
			raw_duration += timedelta(hours=24)

		if is_two_date:
			time_out = time_out + timedelta(hours=24)

		slots.append({
			"shift_type": row.shift_type,
			"time_in": time_in,
			"time_out": time_out,
			"two_date_shift": is_two_date,
			"begin_check_in_before": begin_check_in_before,
			"allow_check_out_after": allow_check_out_after,
			"duration": raw_duration,
		})

	slots.sort(key=lambda s: s["time_in"])
	return slots


def build_slot_windows(slots):
	"""
	Given sorted slots, compute a matching window (start, end) for each slot.
	For overnight slots, day_end is extended beyond 23:59:59.
	"""
	if not slots:
		return []

	day_start = timedelta(0)
	max_time_out = max(s["time_out"] for s in slots)
	day_end = max(timedelta(hours=23, minutes=59, seconds=59), max_time_out)

	windows = []
	for i, slot in enumerate(slots):
		if i == 0:
			win_start = day_start
		else:
			prev_out = slots[i - 1]["time_out"]
			win_start = prev_out + (slot["time_in"] - prev_out) / 2

		if i == len(slots) - 1:
			win_end = day_end
		else:
			next_in = slots[i + 1]["time_in"]
			win_end = slot["time_out"] + (next_in - slot["time_out"]) / 2

		windows.append((win_start, win_end))

	return windows


def assign_punches_to_slots(punches, slots, windows):
	"""
	punches: list of timedeltas sorted ascending.
	         Next-day punches are already offset by +24h.
	Returns a list aligned with slots:
	[{"check_in": timedelta|None, "check_out": timedelta|None}, ...]
	"""
	result = [{"check_in": None, "check_out": None} for _ in slots]

	for punch in punches:
		for idx, (win_start, win_end) in enumerate(windows):
			if win_start <= punch <= win_end:
				slot = slots[idx]
				entry = result[idx]

				if entry["check_in"] is None and entry["check_out"] is None:
					dist_in = abs((punch - slot["time_in"]).total_seconds())
					dist_out = abs((punch - slot["time_out"]).total_seconds())
					if dist_in <= dist_out:
						entry["check_in"] = punch
					else:
						entry["check_out"] = punch
				elif entry["check_in"] is not None and entry["check_out"] is None:
					if punch > entry["check_in"]:
						entry["check_out"] = punch
					else:
						if entry["check_in"] > punch:
							entry["check_out"] = entry["check_in"]
							entry["check_in"] = punch
				elif entry["check_out"] is not None and entry["check_in"] is None:
					if punch < entry["check_out"]:
						entry["check_in"] = punch
					else:
						entry["check_in"] = entry["check_out"]
						entry["check_out"] = punch
				else:
					all_times = [entry["check_in"], entry["check_out"], punch]
					entry["check_in"] = min(all_times)
					entry["check_out"] = max(all_times)
				break

	return result


def format_td(td):
	"""Format a timedelta as HH:MM, handling overnight offsets > 24h."""
	if td is None:
		return ""
	total_seconds = int(td.total_seconds())
	total_seconds = total_seconds % 86400
	hours = total_seconds // 3600
	minutes = (total_seconds % 3600) // 60
	return f"{hours:02d}:{minutes:02d}"


def format_hours(seconds):
	"""Format seconds as Hh Mm Ss using format_duration, blank if zero/None."""
	if not seconds:
		return ""
	return format_duration(seconds, hide_days=True)


# ---------------------------------------------------------------------------
# Core per-employee, per-date computation
# ---------------------------------------------------------------------------

def get_personal_attendance_for_date(em_id, employee_name, att_date, worked_on_holiday=False):
	department = frappe.db.get_value("Employee", em_id, "department")
	slots = get_employee_slots(em_id)

	has_two_date_shift = any(s.get("two_date_shift") for s in slots)

	# --- Holiday / Leave checks -------------------------------------------------
	holiday_list_name = get_holiday_list_for_employee(em_id, False)
	on_holiday = is_holiday(holiday_list_name, att_date) if holiday_list_name else False

	leaves = frappe.db.get_list(
		"Leaves Assigment",
		filters={
			"employee": em_id,
			"from_date": ("<=", att_date),
			"to_date": (">=", att_date),
		}
	)
	on_leave = bool(leaves)

	# --- Punches ----------------------------------------------------------------
	checkins = frappe.db.sql(
		"""
		select time from `tabEmployee Checkin`
		where employee = %s and date(time) = %s
		order by time
		""",
		(em_id, att_date),
		as_dict=1,
	)
	punches = [_to_timedelta(c['time']) for c in checkins]

	if has_two_date_shift:
		two_date_slots = [s for s in slots if s.get("two_date_shift")]

		overnight_checkout_limit = max(
			s["time_out"] - timedelta(hours=24) for s in two_date_slots
		)
		overnight_time_in = min(s["time_in"] for s in two_date_slots)

		begin_check_in_before = min(s["begin_check_in_before"] for s in two_date_slots)
		allow_check_out_after = max(s["allow_check_out_after"] for s in two_date_slots)

		punches = [
			p for p in punches
			if p >= overnight_time_in - begin_check_in_before
		]

		next_date = att_date + timedelta(days=1)
		next_day_checkins = frappe.db.sql(
			"""
			select time from `tabEmployee Checkin`
			where employee = %s and date(time) = %s
			order by time
			""",
			(em_id, next_date),
			as_dict=1,
		)
		next_punches = [
			_to_timedelta(c['time']) + timedelta(hours=24)
			for c in next_day_checkins
			if _to_timedelta(c['time']) <= overnight_checkout_limit + allow_check_out_after
		]
		punches = punches + next_punches

	punches.sort()

	# Expected hours for this date = sum of assigned slot durations,
	# zero on holiday / leave days (no work expected).
	expected_seconds = sum((s["duration"].total_seconds() for s in slots), 0)

	row = {
		"employee": em_id,
		"employee_name": employee_name,
		"department": department,
		"attendance_date": att_date,
		"shift": ", ".join({s["shift_type"] for s in slots}) if slots else "",
		"am_in": "",
		"am_out": "",
		"pm_in": "",
		"pm_out": "",
		"worked_hours": "",
		"expected_hours": "",
		"difference": "",
		"status": "",
		"holiday": "",
		"_worked_seconds": 0,
		"_expected_seconds": 0,
	}

	# --- Holiday / Leave precedence ---------------------------------------------
	if on_leave:
		row["status"] = "On Leave"
		row["expected_hours"] = ""
		return row

	if on_holiday:
		if punches and worked_on_holiday:
			row["status"] = "Present"
			row["holiday"] = "Holiday"
		else:
			row["status"] = "Holiday"
			row["expected_hours"] = ""
			return row

	if not slots:
		if punches:
			row["status"] = "Present"
		elif not on_holiday:
			row["status"] = "Absent" if att_date != getdate() else ""
		return row

	# --- Slot matching / hours ---------------------------------------------------
	total_seconds_worked = 0
	complete_slots = 0
	expected_slots = len(slots)
	filled_slots = 0

	if has_two_date_shift:
		expected_slots = 1

		if punches:
			first_in = punches[0]
			last_out = punches[-1] if len(punches) > 1 else None

			row["am_in"] = format_td(first_in)
			row["am_out"] = format_td(last_out)

			filled_slots = 1
			if last_out is not None and last_out > first_in:
				total_seconds_worked = (last_out - first_in).total_seconds()
				complete_slots = 1
	else:
		windows = build_slot_windows(slots)
		assigned = assign_punches_to_slots(punches, slots, windows)

		for idx, slot in enumerate(slots):
			entry = assigned[idx]
			c_in = entry["check_in"]
			c_out = entry["check_out"]

			if c_in is not None or c_out is not None:
				filled_slots += 1

			if c_in is not None and c_out is not None:
				total_seconds_worked += (c_out - c_in).total_seconds()
				complete_slots += 1

			if idx == 0:
				row["am_in"] = format_td(c_in)
				row["am_out"] = format_td(c_out)
			elif idx == 1:
				row["pm_in"] = format_td(c_in)
				row["pm_out"] = format_td(c_out)

	row["worked_hours"] = format_hours(total_seconds_worked)
	row["expected_hours"] = format_hours(expected_seconds)
	row["_worked_seconds"] = total_seconds_worked
	row["_expected_seconds"] = expected_seconds

	diff_seconds = total_seconds_worked - expected_seconds
	if expected_seconds:
		sign = "-" if diff_seconds < 0 else ""
		row["difference"] = sign + format_hours(abs(diff_seconds)) if diff_seconds else "0h"

	# --- Status -----------------------------------------------------------------
	if row["status"]:
		pass
	elif filled_slots == 0:
		row["status"] = "Absent" if att_date != getdate() else ""
	elif complete_slots == expected_slots:
		row["status"] = "Present"
	else:
		row["status"] = "Half Day"

	return row


# ---------------------------------------------------------------------------
# Report data assembly
# ---------------------------------------------------------------------------

def get_data(filters):
	from_date = getdate(filters.get('from_date'))
	to_date = getdate(filters.get('to_date'))
	worked_on_holiday = filters.get('worked_on_holiday')

	roles = frappe.get_roles(frappe.session.user)

	emp_filters = {'status': 'Active'}
	if 'Medical Director' in roles and frappe.session.user != "Administrator":
		emp_filters['department'] = 'Medical Department - RS'
	elif "HR Manager" not in roles and 'Medical Director' not in roles:
		emp_filters['department'] = 'No Depart - RS'

	if filters.get('employee'):
		emp_filters['name'] = filters.get('employee')

	if filters.get('department') and 'name' not in emp_filters:
		if "HR Manager" in roles or frappe.session.user == "Administrator":
			emp_filters['department'] = filters.get('department')

	emp_list = frappe.db.get_list(
		"Employee",
		filters=emp_filters,
		fields=['name as employee', 'employee_name'],
		page_length=1000,
	)

	data = []
	current = from_date
	while current <= to_date:
		for em in emp_list:
			row = get_personal_attendance_for_date(
				em['employee'], em['employee_name'], current, worked_on_holiday
			)
			if row:
				if worked_on_holiday and not row.get("holiday"):
					continue
				data.append(row)
		current += timedelta(days=1)

	data = sorted(data, key=lambda x: (x['employee_name'], x['attendance_date']))
	return data


def get_report_summary(data):
	"""
	Returns summary cards (worked / expected / difference totals)
	shown above the report table, using Frappe's standard
	report_summary mechanism instead of an appended grid row.
	"""
	total_worked_seconds = sum(r.get("_worked_seconds", 0) for r in data)
	total_expected_seconds = sum(r.get("_expected_seconds", 0) for r in data)
	total_diff_seconds = total_worked_seconds - total_expected_seconds

	sign = "-" if total_diff_seconds < 0 else ""
	diff_label = (
		sign + format_hours(abs(total_diff_seconds)) if total_diff_seconds else "0h"
	)

	if total_diff_seconds < 0:
		diff_indicator = "red"
	elif total_diff_seconds > 0:
		diff_indicator = "green"
	else:
		diff_indicator = "blue"

	return [
		# {
		# 	"value": format_hours(total_worked_seconds) or "0h",
		# 	"label": _("Total Worked Hours"),
		# 	"datatype": "Data",
		# 	"indicator": "blue",
		# },
		# {
		# 	"value": format_hours(total_expected_seconds) or "0h",
		# 	"label": _("Total Expected Hours"),
		# 	"datatype": "Data",
		# 	"indicator": "blue",
		# },
		# {
		# 	"value": diff_label,
		# 	"label": _("Difference"),
		# 	"datatype": "Data",
		# 	"indicator": diff_indicator,
		# },
	]


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def get_columns():
	return [
		{
			"label": _("Date"),
			"fieldtype": "Date",
			"fieldname": "attendance_date",
			"width": 100,
		},
		{
			"label": _("Employee"),
			"fieldtype": "Link",
			"fieldname": "employee",
			"options": "Employee",
			"width": 130,
		},
		{
			"label": _("Employee Name"),
			"fieldtype": "Data",
			"fieldname": "employee_name",
			"width": 160,
		},
		{
			"label": _("Department"),
			"fieldtype": "Data",
			"fieldname": "department",
			"width": 140,
		},
		{
			"label": _("Status"),
			"fieldtype": "Data",
			"fieldname": "status",
			"width": 90,
		},
		{
			"label": _("Shift"),
			"fieldtype": "Data",
			"fieldname": "shift",
			"width": 220,
		},
		{
			"label": _("AM In"),
			"fieldtype": "Data",
			"fieldname": "am_in",
			"width": 70,
		},
		{
			"label": _("AM Out"),
			"fieldtype": "Data",
			"fieldname": "am_out",
			"width": 70,
		},
		{
			"label": _("PM In"),
			"fieldtype": "Data",
			"fieldname": "pm_in",
			"width": 70,
		},
		{
			"label": _("PM Out"),
			"fieldtype": "Data",
			"fieldname": "pm_out",
			"width": 70,
		},
		{
			"label": _("Worked Hours"),
			"fieldtype": "Data",
			"fieldname": "worked_hours",
			"width": 100,
		},
		{
			"label": _("Expected Hours"),
			"fieldtype": "Data",
			"fieldname": "expected_hours",
			"width": 110,
		},
		{
			"label": _("Difference"),
			"fieldtype": "Data",
			"fieldname": "difference",
			"width": 100,
		},
	]