# Copyright (c) 2023, Rasiin and contributors
# For license information, please see license.txt

import io

import frappe
from frappe import _
from frappe.utils import getdate, format_duration, add_days
from datetime import timedelta, time as dt_time
from erpnext.setup.doctype.holiday_list.holiday_list import is_holiday


def get_effective_holiday_list(employee):
	"""
	Resolves the Holiday List to use for this employee, with an explicit
	fallback chain (rather than relying on erpnext's default behaviour,
	which varies by version):

	  1. Employee.holiday_list, if set
	  2. Employee.company -> Company.default_holiday_list
	  3. HR Settings.default_holiday_list, if that field exists in this install
	"""
	holiday_list = frappe.db.get_value("Employee", employee, "holiday_list")
	if holiday_list:
		return holiday_list

	company = frappe.db.get_value("Employee", employee, "company")
	if company:
		holiday_list = frappe.get_cached_value("Company", company, "default_holiday_list")
		if holiday_list:
			return holiday_list

	try:
		return frappe.db.get_single_value("HR Settings", "default_holiday_list")
	except Exception:
		return None


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

	The boundary between two consecutive slots is the earliest moment a
	check-in for the LATER slot is accepted:

	    boundary = next_slot.time_in - next_slot.begin_check_in_before

	Everything before that boundary belongs to the earlier slot, so someone
	who stays well past the end of their morning shift still has that punch
	recorded as the morning check-out rather than being misread as an early
	afternoon check-in.

	This replaces the old midpoint rule, which split the gap in half and so
	depended on how long the break happened to be — a 13:29 departure landed
	in the morning slot while a 14:04 departure flipped to the afternoon one.

	The boundary is never allowed to fall before the earlier slot's own
	time_out. For overnight slots, day_end is extended beyond 23:59:59.
	"""
	if not slots:
		return []

	day_start = timedelta(0)
	max_time_out = max(s["time_out"] for s in slots)
	day_end = max(timedelta(hours=23, minutes=59, seconds=59), max_time_out)

	# boundaries[i] separates slot i from slot i+1
	boundaries = []
	for i in range(len(slots) - 1):
		nxt = slots[i + 1]
		boundary = nxt["time_in"] - nxt["begin_check_in_before"]

		# a long check-in grace must not reach back past the previous
		# slot's own end time
		prev_out = slots[i]["time_out"]
		if boundary < prev_out:
			boundary = prev_out

		boundaries.append(boundary)

	windows = []
	for i, slot in enumerate(slots):
		win_start = day_start if i == 0 else boundaries[i - 1]
		if i == len(slots) - 1:
			win_end = day_end
		else:
			# keep the two windows disjoint: a punch exactly on the boundary
			# is a check-in for the later slot
			win_end = boundaries[i] - timedelta(seconds=1)

		windows.append((win_start, win_end))

	return windows


def assign_punches_to_slots(punches, slots, windows):
	"""
	punches: list of timedeltas sorted ascending.
	         Next-day punches are already offset by +24h.
	Returns a list aligned with slots:
	[{"check_in": timedelta|None, "check_out": timedelta|None}, ...]

	A slot never ends up with check_out <= check_in: if the only punches
	that land in a slot are effectively the same moment (a double swipe),
	the punch is kept as the check-in and check_out is left blank.
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
					elif punch < entry["check_in"]:
						entry["check_out"] = entry["check_in"]
						entry["check_in"] = punch
					# equal -> duplicate swipe, ignore
				elif entry["check_out"] is not None and entry["check_in"] is None:
					if punch < entry["check_out"]:
						entry["check_in"] = punch
					elif punch > entry["check_out"]:
						entry["check_in"] = entry["check_out"]
						entry["check_out"] = punch
					# equal -> duplicate swipe, ignore
				else:
					all_times = [entry["check_in"], entry["check_out"], punch]
					entry["check_in"] = min(all_times)
					entry["check_out"] = max(all_times)
				break

	# Final guard: an In/Out pair at the same time is a double swipe,
	# not a zero-length work period. Keep it as In only.
	for entry in result:
		if entry["check_in"] is not None and entry["check_out"] is not None:
			if entry["check_out"] <= entry["check_in"]:
				entry["check_out"] = None

	return result


# How close two swipes have to be before we treat them as the same punch.
# Raise this (e.g. minutes=5) if your readers commonly register repeats.
PUNCH_DEDUPE_WINDOW = timedelta(minutes=1)


def dedupe_punches(punches, window=PUNCH_DEDUPE_WINDOW):
	"""
	Collapse repeated swipes at (almost) the same moment into a single punch.

	Without this, two Employee Checkin rows a few seconds apart become an
	In and an Out that both format to the same HH:MM.
	"""
	kept = []
	for p in sorted(p for p in punches if p is not None):
		if kept and (p - kept[-1]) <= window:
			continue
		kept.append(p)
	return kept


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


def seconds_to_hours(seconds):
	"""Convert seconds to decimal hours, rounded to 2 places."""
	if not seconds:
		return 0
	return round(seconds / 3600, 2)


# ---------------------------------------------------------------------------
# Core per-employee, per-date computation
# ---------------------------------------------------------------------------

def get_personal_attendance_for_date(em_id, employee_name, att_date, worked_on_holiday=False, worked_on_leave=False):
	department = frappe.db.get_value("Employee", em_id, "department")
	slots = get_employee_slots(em_id)

	has_two_date_shift = any(s.get("two_date_shift") for s in slots)

	# --- Holiday / Leave checks -------------------------------------------------
	holiday_list_name = get_effective_holiday_list(em_id)
	on_holiday = is_holiday(holiday_list_name, att_date) if holiday_list_name else False

	leaves = frappe.db.get_list(
		"Leaves Assigment",
		filters={
			"employee": em_id,
			"from_date": ("<=", att_date),
			"to_date": (">=", att_date),
			"docstatus": 1
		},
		fields=["leave_type"],
	)
	on_leave = bool(leaves)
	leave_type = leaves[0].get("leave_type") if leaves else None

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

	# Sorts and drops repeat swipes registered within PUNCH_DEDUPE_WINDOW.
	punches = dedupe_punches(punches)

	# Expected hours for this date = sum of assigned slot durations,
	# zero on holiday / leave days (no work expected).
	expected_seconds = sum((s["duration"].total_seconds() for s in slots), 0)

	# Future dates: nothing is due yet, so no mandatory/less hours and no "Absent".
	if att_date > getdate():
		expected_seconds = 0

	# Worked on a holiday: nothing was expected, so every worked hour is extra.
	# (Leave takes priority over holiday, handled separately below.)
	if on_holiday and not on_leave and punches and worked_on_holiday:
		expected_seconds = 0

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
		"worked_hours": None,
		"expected_hours": None,
		"difference": None,
		"status": "",
		"holiday": "",
		"leave_worked": "",
		"leave_type": leave_type,
		"_worked_seconds": 0,
		"_expected_seconds": 0,
		"_first_in": None,
		"_first_slot_start": None,
		"_slots": slots,
	}

	# --- Holiday / Leave precedence ---------------------------------------------
	if on_leave:
		if punches and worked_on_leave:
			# On leave but came in for a shift: fall through so the punches
			# are matched to slots and hours are calculated for that shift.
			row["status"] = "Present"
			row["leave_worked"] = "Leave"
		else:
			row["status"] = "On Leave"
			row["expected_hours"] = None
			return row
	elif on_holiday:
		if punches and worked_on_holiday:
			row["status"] = "Present"
			row["holiday"] = "Holiday"
		else:
			row["status"] = "Holiday"
			row["expected_hours"] = None
			return row

	if not slots:
		if punches:
			row["status"] = "Present"
		elif not on_holiday:
			row["status"] = "Absent" if att_date < getdate() else ""
		return row

	# --- Slot matching / hours ---------------------------------------------------
	total_seconds_worked = 0
	complete_slots = 0
	expected_slots = len(slots)
	filled_slots = 0
	filled_duration = 0  # seconds of the shift(s) the employee actually attended

	if has_two_date_shift:
		expected_slots = 1

		if punches:
			first_in = punches[0]
			last_out = punches[-1] if len(punches) > 1 else None

			# A single punch (or an out that is not strictly later than the in)
			# is recorded as an In only, never as In == Out.
			if last_out is not None and last_out <= first_in:
				last_out = None

			row["am_in"] = format_td(first_in)
			row["am_out"] = format_td(last_out)
			row["_first_in"] = first_in

			filled_slots = 1
			filled_duration = sum(
				s["duration"].total_seconds() for s in slots if s.get("two_date_shift")
			)
			if last_out is not None:
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
				filled_duration += slot["duration"].total_seconds()

			if c_in is not None and c_out is not None:
				total_seconds_worked += (c_out - c_in).total_seconds()
				complete_slots += 1

			if idx == 0:
				row["am_in"] = format_td(c_in)
				row["am_out"] = format_td(c_out)
				row["_first_in"] = c_in
			elif idx == 1:
				row["pm_in"] = format_td(c_in)
				row["pm_out"] = format_td(c_out)

			# On a leave day the first attended shift may be the 2nd one:
			# measure early/late against that shift's start.
			if row["leave_worked"] and row["_first_in"] is None and c_in is not None:
				row["_first_in"] = c_in
				row["_first_slot_start"] = slot["time_in"]

	# Leave day with punches: only the shift(s) attended are expected.
	if row["leave_worked"]:
		expected_seconds = filled_duration

	row["worked_hours"] = seconds_to_hours(total_seconds_worked) if total_seconds_worked else None
	row["expected_hours"] = seconds_to_hours(expected_seconds) if expected_seconds else None
	row["_worked_seconds"] = total_seconds_worked
	row["_expected_seconds"] = expected_seconds

	if expected_seconds:
		diff_seconds = total_seconds_worked - expected_seconds
		row["difference"] = seconds_to_hours(diff_seconds) if diff_seconds else 0

	# --- Status -----------------------------------------------------------------
	if row["status"]:
		pass
	elif filled_slots == 0:
		row["status"] = "Absent" if att_date < getdate() else ""
	elif complete_slots == expected_slots:
		row["status"] = "Present"
	else:
		row["status"] = "Half Day"

	return row


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

@frappe.whitelist()
def debug_slot_windows(employee, day):
	"""
	Shows each slot's matching window for one employee on one date, plus
	that day's punches and which slot each landed in.

	  /api/method/<app>.<...>.debug_slot_windows?employee=EMP-00026&day=2026-08-02

	The window boundaries are what decide whether a late departure counts as
	this slot's check-out or the next slot's check-in.
	"""
	att_date = getdate(day)
	slots = get_employee_slots(employee)
	windows = build_slot_windows(slots)

	checkins = frappe.db.sql(
		"""
		select time from `tabEmployee Checkin`
		where employee = %s and date(time) = %s
		order by time
		""",
		(employee, att_date),
		as_dict=1,
	)
	punches = dedupe_punches([_to_timedelta(c["time"]) for c in checkins])

	punch_report = []
	for p in punches:
		landed = None
		for i, (ws_, we_) in enumerate(windows):
			if ws_ <= p <= we_:
				landed = i
				break
		punch_report.append({"time": format_td(p), "slot": landed})

	assigned = assign_punches_to_slots(punches, slots, windows)

	return {
		"slots": [
			{
				"shift_type": s["shift_type"],
				"time_in": format_td(s["time_in"]),
				"time_out": format_td(s["time_out"]),
				"begin_check_in_before_mins": int(s["begin_check_in_before"].total_seconds() // 60),
				"allow_check_out_after_mins": int(s["allow_check_out_after"].total_seconds() // 60),
				"window_start": format_td(windows[i][0]),
				"window_end": format_td(windows[i][1]),
			}
			for i, s in enumerate(slots)
		],
		"punches": punch_report,
		"result": [
			{"in": format_td(e["check_in"]), "out": format_td(e["check_out"])}
			for e in assigned
		],
	}


@frappe.whitelist()
def debug_calendar_day(employee, day):
	"""
	Explains why a day is (or is not) flagged "(LV)" / "(H)".

	  /api/method/<app>.<...>.debug_calendar_day?employee=EMP-00026&day=2026-07-02

	Shows every Leaves Assigment record covering that date INCLUDING drafts
	(only docstatus 1 counts), the day's punches, and what the report computed.
	"""
	att_date = getdate(day)
	employee_name = frappe.db.get_value("Employee", employee, "employee_name")

	leave_records = frappe.get_all(
		"Leaves Assigment",
		filters={
			"employee": employee,
			"from_date": ("<=", att_date),
			"to_date": (">=", att_date),
		},
		fields=["name", "leave_type", "docstatus", "from_date", "to_date"],
	)

	checkins = frappe.db.sql(
		"""
		select time from `tabEmployee Checkin`
		where employee = %s and date(time) = %s
		order by time
		""",
		(employee, att_date),
		as_dict=1,
	)

	r = get_personal_attendance_for_date(
		employee, employee_name, att_date, worked_on_holiday=True, worked_on_leave=True
	)

	return {
		"leave_records_covering_date": leave_records,
		"note": "Only records with docstatus = 1 (submitted) are treated as leave.",
		"punches": [str(c["time"]) for c in checkins],
		"computed": {
			"status": r.get("status"),
			"holiday": r.get("holiday"),
			"leave_worked": r.get("leave_worked"),
			"am_in": r.get("am_in"), "am_out": r.get("am_out"),
			"pm_in": r.get("pm_in"), "pm_out": r.get("pm_out"),
			"expected_hours": r.get("expected_hours"),
			"worked_hours": r.get("worked_hours"),
		},
	}


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

	# --- Totals row -------------------------------------------------------------
	if data:
		total_worked_seconds = sum(r.get("_worked_seconds", 0) for r in data)
		total_expected_seconds = sum(r.get("_expected_seconds", 0) for r in data)
		total_diff_seconds = total_worked_seconds - total_expected_seconds

		data.append({
			"employee": "",
			"employee_name": _("Total"),
			"department": "",
			"attendance_date": "",
			"shift": "",
			"am_in": "",
			"am_out": "",
			"pm_in": "",
			"pm_out": "",
			"status": "",
			"worked_hours": seconds_to_hours(total_worked_seconds),
			"expected_hours": seconds_to_hours(total_expected_seconds),
			"difference": seconds_to_hours(total_diff_seconds),
			"holiday": "",
			"_worked_seconds": total_worked_seconds,
			"_expected_seconds": total_expected_seconds,
			"is_total_row": 1,
		})

	return data


def get_report_summary(data):
	"""
	Returns summary cards (worked / expected / difference totals)
	shown above the report table, using Frappe's standard
	report_summary mechanism instead of an appended grid row.
	"""
	rows = [r for r in data if not r.get("is_total_row")]
	total_worked_seconds = sum(r.get("_worked_seconds", 0) for r in rows)
	total_expected_seconds = sum(r.get("_expected_seconds", 0) for r in rows)
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
		{
			"value": format_hours(total_worked_seconds) or "0h",
			"label": _("Total Worked Hours"),
			"datatype": "Data",
			"indicator": "blue",
		},
		{
			"value": format_hours(total_expected_seconds) or "0h",
			"label": _("Total Expected Hours"),
			"datatype": "Data",
			"indicator": "blue",
		},
		{
			"value": diff_label,
			"label": _("Difference"),
			"datatype": "Data",
			"indicator": diff_indicator,
		},
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
			"width": 200,
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
			"width": 200,
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
			"fieldtype": "Float",
			"fieldname": "worked_hours",
			"precision": 2,
			"width": 110,
		},
		{
			"label": _("Expected Hours"),
			"fieldtype": "Float",
			"fieldname": "expected_hours",
			"precision": 2,
			"width": 120,
		},
		{
			"label": _("Difference"),
			"fieldtype": "Float",
			"fieldname": "difference",
			"precision": 2,
			"width": 100,
		},
	]


# ---------------------------------------------------------------------------
# Monthly Calendar Excel Export
# (one row per calendar day, matching the "Monthly Attendance" sheet)
# ---------------------------------------------------------------------------

# Map Leaves Assigment.leave_type values to the single/short codes used in
# the sheet's legend. Adjust the keys on the left to match your actual
# Leave Type doctype names.
LEAVE_TYPE_CODE_MAP = {
	"Annual Leave": "L",
	"Compassionate Leave": "C",
	"Maternity Leave": "P/M",
	"Paternity Leave": "P/M",
}


def _late_in_minutes(row):
	"""
	Minutes the first check-in was after the shift's configured start time.
	0 if on time/early or if there is no first check-in / no configured shift.

	Kept for any caller that still wants a plain minute count; the calendar
	itself now uses _late_in_seconds so it can render "1h 16m".
	"""
	slots = row.get("_slots") or []
	first_in = row.get("_first_in")
	if not slots or first_in is None:
		return 0

	shift_start = slots[0]["time_in"]
	late = (first_in - shift_start)
	late_minutes = late.total_seconds() / 60
	return round(late_minutes) if late_minutes > 0 else 0


def _late_in_seconds(row):
	"""
	Seconds the first check-in was after the shift's configured start time.
	0 if on time/early or if there is no first check-in / no configured shift.

	Seconds (rather than minutes) so the value can be handed straight to
	format_hours() and totalled without losing precision.
	"""
	slots = row.get("_slots") or []
	first_in = row.get("_first_in")
	if not slots or first_in is None:
		return 0

	start = row.get("_first_slot_start")
	if start is None:
		start = slots[0]["time_in"]

	late = (first_in - start).total_seconds()
	return int(late) if late > 0 else 0


def _early_in_seconds(row):
	"""
	Seconds the first check-in was BEFORE the shift's configured start time.
	0 if on time/late or if there is no first check-in / no configured shift.
	"""
	slots = row.get("_slots") or []
	first_in = row.get("_first_in")
	if not slots or first_in is None:
		return 0

	start = row.get("_first_slot_start")
	if start is None:
		start = slots[0]["time_in"]

	early = (start - first_in).total_seconds()
	return int(early) if early > 0 else 0


def _mandatory_display(hours):
	"""
	Mandatory hours as a clean whole number for the calendar: 8 (full day),
	4 (one shift) or "" (nothing expected: holiday / leave).

	Rounds the computed decimal to the nearest whole hour, so 7.98 -> 8 and
	4.02 -> 4, and returns "" for zero/None. Only affects what is displayed
	and totalled; Less hours / Overtime still use the exact seconds.
	"""
	if not hours:
		return ""
	return int(hours + 0.5) or ""


def _less_hours(mandatory_hours, actual_hours):
	diff = round((mandatory_hours or 0) - (actual_hours or 0), 2)
	return diff if diff > 0 else 0


def _overtime_hours(mandatory_hours, actual_hours):
	diff = round((actual_hours or 0) - (mandatory_hours or 0), 2)
	return diff if diff > 0 else 0


def calendar_totals(rows):
	"""
	Column totals plus a day-count breakdown for the monthly calendar.

	`worked_days` counts any day with at least one punch recorded, so a day
	with a missing check-out still counts as worked — it shows up in
	`less_hours` rather than being dropped from the day count.

	Days worked on a holiday are counted separately in `holiday_worked_days`
	(with their hours in `holiday_worked_hours`) and are NOT also counted in
	`worked_days`.

	Actual hours, Early-in, Late-in, Less hours, and Overtime are all
	accumulated from their raw `_actual_seconds` / `_early_seconds` /
	`_late_seconds` / `_less_seconds` / `_overtime_seconds` values on each row,
	since the displayed values are duration strings like "7h 30m" and can't be
	summed directly. They are formatted once at the end. `mandatory_hours`
	stays a plain decimal total.
	"""
	totals = {
		"mandatory_hours": 0.0,
		"actual_hours": 0,
		"early_in_minutes": 0,
		"late_in_minutes": 0,
		"less_hours": 0,
		"overtime": 0,
		"worked_days": 0,
		"absent_days": 0,
		"leave_days": 0,
		"holiday_days": 0,
		"holiday_worked_days": 0,
		"holiday_worked_hours": 0,
		"leave_worked_days": 0,
		"leave_worked_hours": 0,
	}

	for r in rows:
		totals["mandatory_hours"] += r.get("mandatory_hours") or 0
		totals["actual_hours"] += r.get("_actual_seconds") or 0
		totals["early_in_minutes"] += r.get("_early_seconds") or 0
		totals["late_in_minutes"] += r.get("_late_seconds") or 0
		totals["less_hours"] += r.get("_less_seconds") or 0
		totals["overtime"] += r.get("_overtime_seconds") or 0

		day_type = r.get("day_type")
		if day_type == "absent":
			totals["absent_days"] += 1
		elif day_type == "leave":
			totals["leave_days"] += 1
		elif day_type == "holiday":
			totals["holiday_days"] += 1
		elif day_type == "holiday_worked":
			totals["holiday_worked_days"] += 1
			totals["holiday_worked_hours"] += r.get("_actual_seconds") or 0
		elif day_type == "leave_worked":
			totals["leave_worked_days"] += 1
			totals["leave_worked_hours"] += r.get("_actual_seconds") or 0
		elif r.get("am_in") or r.get("am_out") or r.get("pm_in") or r.get("pm_out"):
			totals["worked_days"] += 1

	totals["mandatory_hours"] = int(round(totals["mandatory_hours"]))
	for key in ("actual_hours", "early_in_minutes", "late_in_minutes",
				"less_hours", "overtime", "holiday_worked_hours", "leave_worked_hours"):
		totals[key] = format_hours(totals[key])

	return totals


def get_calendar_rows(employee, from_date, to_date):
	"""One row per calendar day for a single employee, with the fields
	needed by the monthly calendar sheet.

	Note: `actual_hours`, `early_in_minutes`, `late_in_minutes`, `less_hours`,
	and `overtime` are formatted duration strings (e.g. "7h 30m").
	`mandatory_hours` stays a plain decimal (e.g. 8.0).

	A holiday with punches is flagged `holiday_worked` (day_type
	"holiday_worked"): mandatory hours are zero, so all hours worked count as
	overtime, and early/late-in are not calculated.
	"""
	from_date = getdate(from_date)
	to_date = getdate(to_date)
	employee_name = frappe.db.get_value("Employee", employee, "employee_name")

	rows = []
	current = from_date
	while current <= to_date:
		# worked_on_holiday / worked_on_leave=True so punches on a holiday or
		# a leave day are detected
		day_row = get_personal_attendance_for_date(
			employee, employee_name, current,
			worked_on_holiday=True, worked_on_leave=True
		)

		leave_worked = bool(day_row.get("leave_worked"))
		holiday_worked = (
			day_row.get("holiday") == "Holiday" and day_row.get("status") == "Present"
		)

		mandatory_hours = _mandatory_display(day_row.get("expected_hours"))  # 8 / 4 / ""

		# Early/late make no sense on a holiday (no shift expected). On a leave
		# day they are measured against the first shift actually attended.
		late_seconds = 0 if holiday_worked else _late_in_seconds(day_row)
		early_seconds = 0 if holiday_worked else _early_in_seconds(day_row)

		# Raw seconds (already computed upstream in get_personal_attendance_for_date)
		# so Actual/Less/Overtime can be formatted as durations.
		actual_seconds = day_row.get("_worked_seconds") or 0
		mandatory_seconds = day_row.get("_expected_seconds") or 0
		less_seconds = max(mandatory_seconds - actual_seconds, 0)
		overtime_seconds = max(actual_seconds - mandatory_seconds, 0)

		code = ""
		day_type = ""  # "" | "absent" | "leave" | "holiday" | "holiday_worked" | "leave_worked"
		if leave_worked:
			day_type = "leave_worked"
		elif holiday_worked:
			day_type = "holiday_worked"
		elif day_row["status"] == "Absent":
			code = "A"
			day_type = "absent"
		elif day_row["status"] == "On Leave":
			code = LEAVE_TYPE_CODE_MAP.get(day_row.get("leave_type"), "L")
			day_type = "leave"
		elif day_row["status"] == "Holiday":
			code = "Holiday"
			day_type = "holiday"

		rows.append({
			"date": current,
			"am_in": day_row.get("am_in") or "",
			"am_out": day_row.get("am_out") or "",
			"pm_in": day_row.get("pm_in") or "",
			"pm_out": day_row.get("pm_out") or "",
			"mandatory_hours": mandatory_hours,
			# displayed as durations, e.g. "7h 30m"
			"actual_hours": format_hours(actual_seconds),
			"early_in_minutes": format_hours(early_seconds),
			"late_in_minutes": format_hours(late_seconds),
			"less_hours": format_hours(less_seconds),
			"overtime": format_hours(overtime_seconds),
			# raw values kept so calendar_totals() can sum them
			"_early_seconds": early_seconds,
			"_late_seconds": late_seconds,
			"_actual_seconds": actual_seconds,
			"_less_seconds": less_seconds,
			"_overtime_seconds": overtime_seconds,
			"code": code,
			"day_type": day_type,
			"holiday_worked": holiday_worked,
			"leave_worked": leave_worked,
		})
		current = add_days(current, 1)

	return rows, employee_name


@frappe.whitelist()
def get_personal_attendance_calendar(employee, from_date, to_date):
	"""
	JSON version of the monthly calendar (same day-by-day data as the
	Excel export), for rendering on-screen instead of downloading a file.

	Returns:
	  {
	    "employee": "...", "employee_name": "...",
	    "designation": "...", "department": "...",
	    "rows": [
	      {"date": "2026-07-01", "date_label": "1-Jul",
	       "am_in": "08:40", "am_out": "11:20",
	       "pm_in": "15:50", "pm_out": "20:45", "mandatory_hours": 8.0,
	       "actual_hours": "8h", "early_in_minutes": "", "late_in_minutes": "40m",
	       "less_hours": "", "overtime": "1h", "code": "",
	       "day_type": "", "holiday_worked": false},
	      ...
	    ],
	    "totals": {"mandatory_hours": 176.0, "actual_hours": "168h 30m",
	               "early_in_minutes": "2h 5m", "late_in_minutes": "1h 3m",
	               "less_hours": "7h 30m", "overtime": "12h 18m",
	               "worked_days": 22, "absent_days": 1,
	               "leave_days": 0, "holiday_days": 5,
	               "holiday_worked_days": 1, "holiday_worked_hours": "6h"}
	  }

	Note: `actual_hours`, `early_in_minutes`, `late_in_minutes`, `less_hours`,
	and `overtime` are formatted duration strings, not numbers.
	`mandatory_hours` stays a plain decimal.
	"""
	employee_doc = frappe.get_cached_doc("Employee", employee)
	rows, employee_name = get_calendar_rows(employee, from_date, to_date)
	totals = calendar_totals(rows)

	# The front-end table renders `row.date_label` (e.g. "1-Jul"), so compute
	# it from the raw date *before* overwriting `date` with the ISO string.
	for row in rows:
		date_val = row["date"]
		row["date_label"] = date_val.strftime("%-d-%b") if hasattr(date_val, "strftime") else str(date_val)
		row["date"] = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)

	return {
		"employee": employee,
		"employee_name": employee_name,
		"designation": employee_doc.designation,
		"department": employee_doc.department,
		"rows": rows,
		"totals": totals,
	}


@frappe.whitelist()
def download_monthly_calendar(employee, from_date, to_date):
	"""
	Whitelisted GET endpoint. Streams an .xlsx file matching the
	"Monthly Attendance" calendar sheet for a single employee.

	Call from the browser as a plain GET (not frappe.call), e.g.:
	  /api/method/<app>.<...>.download_monthly_calendar?employee=EMP-00002&from_date=2026-07-01&to_date=2026-07-31
	"""
	from openpyxl import Workbook
	from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
	from openpyxl.utils import get_column_letter

	employee_doc = frappe.get_cached_doc("Employee", employee)
	company = employee_doc.company
	company_display = frappe.db.get_value("Company", company, "company_name") or company or ""

	rows, employee_name = get_calendar_rows(employee, from_date, to_date)
	totals = calendar_totals(rows)

	wb = Workbook()
	ws = wb.active
	ws.title = "Monthly Attendance"

	N_COLS = 11  # A..K
	purple_fill = PatternFill(start_color="7A5EA7", end_color="7A5EA7", fill_type="solid")
	total_fill = PatternFill(start_color="EDE7F4", end_color="EDE7F4", fill_type="solid")
	header_font = Font(bold=True, color="FFFFFF")
	bold_font = Font(bold=True)
	center = Alignment(horizontal="center", vertical="center", wrap_text=True)
	left = Alignment(horizontal="left", vertical="center")
	right = Alignment(horizontal="right", vertical="center")
	thin = Side(style="thin", color="000000")
	box_border = Border(left=thin, right=thin, top=thin, bottom=thin)

	DAY_TYPE_STYLE = {
		"absent": {"fill": "FDE9E9", "font_color": "E11D48"},
		"holiday": {"fill": "DCE6F1", "font_color": "2563EB"},
		"leave": {"fill": "DCE6F1", "font_color": "2563EB"},
	}

	def merge_and_set(row, col_start, col_end, value, fill=None, font=None, align=center, border=True):
		ws.merge_cells(start_row=row, start_column=col_start, end_row=row, end_column=col_end)
		cell = ws.cell(row=row, column=col_start, value=value)
		if fill:
			for c in range(col_start, col_end + 1):
				ws.cell(row=row, column=c).fill = fill
		if font:
			cell.font = font
		cell.alignment = align
		if border:
			for c in range(col_start, col_end + 1):
				ws.cell(row=row, column=c).border = box_border
		return cell

	# --- Title -----------------------------------------------------------------
	merge_and_set(1, 1, N_COLS, f"Monthly Attendance - {company_display}".strip(" -"),
				  font=Font(bold=True, size=14), border=False)

	# --- Employee header block ---------------------------------------------------
	merge_and_set(2, 1, 2, "Employee ID", fill=purple_fill, font=header_font)
	merge_and_set(2, 3, 3, employee, align=left)
	merge_and_set(2, 6, 7, "Employee Name", fill=purple_fill, font=header_font)
	merge_and_set(2, 8, 11, employee_name, align=left)

	merge_and_set(3, 1, 2, "Job title", fill=purple_fill, font=header_font)
	merge_and_set(3, 3, 3, employee_doc.designation or "", align=left)
	merge_and_set(3, 6, 7, "Department", fill=purple_fill, font=header_font)
	merge_and_set(3, 8, 11, employee_doc.department or "", align=left)

	# --- Column headers ----------------------------------------------------------
	header_row = 5
	headers = ["Date", "In", "Out", "In", "Out", "Mandatory\nHours",
			   "Actual hours\nworked", "Early-in", "Late-in",
			   "Less Hours\nworked", "Overtime"]
	for i, label in enumerate(headers, start=1):
		merge_and_set(header_row, i, i, label, fill=purple_fill, font=header_font)

	# --- Day rows ------------------------------------------------------------------
	r = header_row + 1
	for day in rows:
		date_text = day["date"].strftime("%-d-%b") if hasattr(day["date"], "strftime") else str(day["date"])
		if day.get("holiday_worked"):
			date_text += " (H)"
		elif day.get("leave_worked"):
			date_text += " (LV)"
		date_cell = ws.cell(row=r, column=1, value=date_text)
		if day.get("holiday_worked"):
			date_cell.fill = PatternFill(start_color="FFF4CE", end_color="FFF4CE", fill_type="solid")
			date_cell.font = Font(bold=True, color="B45309")
		elif day.get("leave_worked"):
			date_cell.fill = PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid")
			date_cell.font = Font(bold=True, color="2563EB")

		if day["code"]:
			style = DAY_TYPE_STYLE.get(day.get("day_type"))
			code_fill = PatternFill(start_color=style["fill"], end_color=style["fill"], fill_type="solid") if style else None
			code_font = Font(bold=True, color=style["font_color"]) if style else Font(bold=True)
			merge_and_set(r, 2, 5, day["code"], fill=code_fill, font=code_font, align=center)
		else:
			ws.cell(row=r, column=2, value=day["am_in"])
			ws.cell(row=r, column=3, value=day["am_out"])
			ws.cell(row=r, column=4, value=day["pm_in"])
			ws.cell(row=r, column=5, value=day["pm_out"])

		ws.cell(row=r, column=6, value=day["mandatory_hours"] or None)
		ws.cell(row=r, column=7, value=day["actual_hours"] or None)
		ws.cell(row=r, column=8, value=day["early_in_minutes"] or None)
		ws.cell(row=r, column=9, value=day["late_in_minutes"] or None)
		ws.cell(row=r, column=10, value=day["less_hours"] or None)
		ws.cell(row=r, column=11, value=day["overtime"] or None)

		for c in range(1, N_COLS + 1):
			cell = ws.cell(row=r, column=c)
			cell.border = box_border
			# keep the amber holiday-worked styling on the date cell
			cell.alignment = center
		r += 1

	# --- Totals row ----------------------------------------------------------------
	totals_row = r
	merge_and_set(totals_row, 1, 5, "Total", fill=total_fill, font=bold_font, align=right)
	for col, key in (
		(6, "mandatory_hours"),
		(7, "actual_hours"),
		(8, "early_in_minutes"),
		(9, "late_in_minutes"),
		(10, "less_hours"),
		(11, "overtime"),
	):
		cell = ws.cell(row=totals_row, column=col, value=totals[key] or None)
		cell.fill = total_fill
		cell.font = bold_font
		cell.alignment = center
		cell.border = box_border
	r = totals_row + 1

	# --- Day count summary ----------------------------------------------------------
	summary_row = r
	summary = (
		f"Worked days: {totals['worked_days']}    "
		f"Absent: {totals['absent_days']}    "
		f"Leave: {totals['leave_days']}    "
		f"Holiday: {totals['holiday_days']}    "
		f"Worked on holiday: {totals['holiday_worked_days']} ({totals['holiday_worked_hours'] or '0h'})    "
		f"Worked on leave: {totals['leave_worked_days']} ({totals['leave_worked_hours'] or '0h'})"
	)
	merge_and_set(summary_row, 1, N_COLS, summary, font=bold_font, align=left, border=False)
	r = summary_row + 1

	# --- Signature row ---------------------------------------------------------
	r += 1
	sig_row = r
	merge_and_set(sig_row, 1, 3, "Employee signature", border=False, align=Alignment(horizontal="center"))
	merge_and_set(sig_row, 4, 7, "Manager/Supervisor signature", border=False, align=Alignment(horizontal="center"))
	merge_and_set(sig_row, 8, 11, "HR Manager Signature", border=False, align=Alignment(horizontal="center"))
	for c in range(1, N_COLS + 1):
		ws.cell(row=sig_row - 1, column=c).border = Border(bottom=thin)

	# --- Legend row --------------------------------------------------------------
	legend_row = sig_row + 2
	legend_items = [
		(1, 2, "A - Absence"),
		(3, 4, "L - Annual leave"),
		(5, 6, "C - Compassionate leave"),
		(7, 8, "P/M - Maternity"),
		(9, 10, "UA - Unjustified Absence"),
		(11, 11, "(H) - Worked on holiday"),
	]
	for start, end, label in legend_items:
		merge_and_set(legend_row, start, end, label, border=False, align=left)
	merge_and_set(legend_row + 1, 1, 5, "(LV) - On leave, attended a shift", border=False, align=left)

	# --- Column widths -------------------------------------------------------------
	widths = [12, 8, 8, 8, 8, 11, 13, 10, 10, 11, 10]
	for i, w in enumerate(widths, start=1):
		ws.column_dimensions[get_column_letter(i)].width = w

	# --- Stream the file ------------------------------------------------------------
	buffer = io.BytesIO()
	wb.save(buffer)
	buffer.seek(0)

	filename = f"Monthly Attendance - {employee_name} - {getdate(from_date).strftime('%b %Y')}.xlsx"
	frappe.local.response.filename = filename
	frappe.local.response.filecontent = buffer.getvalue()
	frappe.local.response.type = "binary"