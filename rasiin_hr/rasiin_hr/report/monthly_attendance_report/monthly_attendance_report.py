"""
Monthly Attendance Report — Script Report backend
==================================================

Drop this in as the .py file behind the "Monthly attendance report" Script
Report (same folder as monthly_attendance_report.js). Frappe calls
`execute(filters)` and expects it to return (columns, data).

Each day/shift produces TWO REAL COLUMNS (fieldname `d{day_idx}_s{slot_idx}_in`
/ `..._out`) instead of one multi-line text blob per day. This lets the
report-view grid render actual In/Out cells the same way the Excel export
does — the formatter in monthly_attendance_report.js then colors them to
match the mock-up.

NOTE: Frappe's datatable only supports a single header row, so the day
label and In/Out label are combined into one header per column
(e.g. "8 Jul In"), rather than the two stacked rows Excel can do.

LEAVE HANDLING
--------------
Day status precedence, matching the Personal Attendance Report:
  1. On Leave   (Leaves Assigment record covering that date)
  2. Holiday    (date is on the employee's Holiday List, no check-ins)
  3. Absent     (Attendance.status == "Absent", no check-ins, not on leave/holiday)
  4. Present    (normal shift slot rendering)

Adjust LEAVE_TYPE_CODE_MAP below if your Leave Type doctype names differ.
"""

from datetime import datetime, timedelta, date, time as time_cls

import frappe

SHIFT_CHILD_TABLE_FIELDNAME = "shifts"
SHIFT_TYPE_SUBFIELD = "shift_type"
SHIFT_TIME_IN_SUBFIELD = "time_in"
SHIFT_TIME_OUT_SUBFIELD = "time_out"
SHIFT_MATCH_BUFFER_HOURS = 2

# Map Leaves Assigment.leave_type values to the short codes used in the
# grid/legend. Adjust the keys on the left to match your actual Leave Type
# doctype names.
LEAVE_TYPE_CODE_MAP = {
    "Annual Leave": "L",
    "Compassionate Leave": "C",
    "Maternity Leave": "P/M",
    "Paternity Leave": "P/M",
}

_SHIFT_CONFIG_CACHE = {}


def _detect_shift_config():
    """
    Auto-detects the Employee child table that holds shift slots, instead
    of relying on hardcoded fieldnames (which may not match your instance's
    "Shift Type Slot" table). Falls back to the hardcoded constants above
    if nothing matching is found.
    """
    if _SHIFT_CONFIG_CACHE:
        return _SHIFT_CONFIG_CACHE

    meta = frappe.get_meta("Employee")
    for df in meta.get_table_fields():
        try:
            child_meta = frappe.get_meta(df.options)
        except Exception:
            continue

        type_field = time_in_field = time_out_field = None
        for f in child_meta.fields:
            label = (f.label or "").lower()
            fname = (f.fieldname or "").lower()

            if f.fieldtype in ("Link", "Select") and ("shift" in fname or "shift" in label):
                type_field = f.fieldname
            if f.fieldtype == "Time" and ("in" in fname or "in" in label):
                time_in_field = f.fieldname
            if f.fieldtype == "Time" and ("out" in fname or "out" in label):
                time_out_field = f.fieldname

        if type_field and time_in_field and time_out_field:
            _SHIFT_CONFIG_CACHE.update({
                "table_field": df.fieldname,
                "type_field": type_field,
                "time_in_field": time_in_field,
                "time_out_field": time_out_field,
            })
            return _SHIFT_CONFIG_CACHE

    # Nothing auto-detected — fall back to the hardcoded guesses
    _SHIFT_CONFIG_CACHE.update({
        "table_field": SHIFT_CHILD_TABLE_FIELDNAME,
        "type_field": SHIFT_TYPE_SUBFIELD,
        "time_in_field": SHIFT_TIME_IN_SUBFIELD,
        "time_out_field": SHIFT_TIME_OUT_SUBFIELD,
    })
    return _SHIFT_CONFIG_CACHE


@frappe.whitelist()
def get_attendance_grid(from_date, to_date, department=None, employee=None):
    """
    Returns data shaped for a real HTML table with true merged header
    cells (colspan), used by the custom 'Monthly Attendance Grid' page —
    the standard report-view grid can't do true merged headers.
    """
    filters = {
        "from_date": from_date,
        "to_date": to_date,
        "department": department,
        "employee": employee,
    }
    day_list = _date_range(_parse_date(from_date), _parse_date(to_date))
    employees_data, max_shifts = get_report_data(filters, day_list)

    days_out = [d.strftime("%-d %b") for d in day_list]

    rows = []
    for emp in employees_data:
        day_cells = []
        for d in day_list:
            info = emp["day_values"][d]
            if info["leave"]:
                code = LEAVE_TYPE_CODE_MAP.get(info.get("leave_type"), "L")
                day_cells.append({"type": "leave", "code": code})
            elif info["holiday"]:
                day_cells.append({"type": "holiday"})
            elif info["absent"]:
                day_cells.append({"type": "absent"})
            else:
                slots = []
                for in_t, out_t in info["slots"]:
                    slots.append({
                        "in": in_t.strftime("%-H:%M") if in_t else "",
                        "out": out_t.strftime("%-H:%M") if out_t else "",
                    })
                # pad to max_shifts so every row has the same column count
                while len(slots) < max_shifts:
                    slots.append({"in": "", "out": ""})
                day_cells.append({"type": "normal", "slots": slots})

        rows.append({
            "employee": emp["employee"],
            "employee_name": emp["employee_name"],
            "shift_names": emp["shift_names"],
            "days": day_cells,
        })

    return {"days": days_out, "max_shifts": max_shifts, "rows": rows}


@frappe.whitelist()
def debug_shift_detection():
    """Call via /api/method/<app>.<module>.monthly_attendance_report.debug_shift_detection
    to confirm which Employee child table/fields the report is using."""
    return _detect_shift_config()


def execute(filters=None):
    filters = frappe._dict(filters or {})
    from_date = _parse_date(filters.get("from_date") or frappe.utils.nowdate())
    to_date = _parse_date(filters.get("to_date") or frappe.utils.nowdate())
    day_list = _date_range(from_date, to_date)

    employees_data, max_shifts = get_report_data(filters, day_list)
    columns = get_columns(day_list, max_shifts)
    data = get_data(employees_data, day_list, max_shifts)

    return columns, data


# ═══════════════════════════════════════════════════════════
#  COLUMNS / DATA SHAPING
# ═══════════════════════════════════════════════════════════
def get_columns(day_list, max_shifts):
    columns = [
        {"fieldname": "employee", "label": "Employee ID", "fieldtype": "Link",
         "options": "Employee", "width": 110},
        {"fieldname": "employee_name", "label": "Employee Name", "fieldtype": "Data", "width": 170},
        {"fieldname": "shift_names", "label": "Default Shift", "fieldtype": "Data", "width": 190},
    ]
    for day_idx, d in enumerate(day_list):
        date_label = d.strftime("%-d %b")
        for slot_idx in range(max_shifts):
            columns.append({
                "fieldname": f"d{day_idx}_s{slot_idx}_in",
                "label": f"{date_label}\nIn",
                "fieldtype": "Data",
                "width": 95,
            })
            columns.append({
                "fieldname": f"d{day_idx}_s{slot_idx}_out",
                "label": f"{date_label}\nOut",
                "fieldtype": "Data",
                "width": 95,
            })
    return columns


def get_data(employees_data, day_list, max_shifts):
    rows = []
    for emp in employees_data:
        row = {
            "employee": emp["employee"],
            "employee_name": emp["employee_name"],
            "shift_names": emp["shift_names"],
        }
        for day_idx, d in enumerate(day_list):
            info = emp["day_values"][d]

            if info["leave"]:
                code = LEAVE_TYPE_CODE_MAP.get(info.get("leave_type"), "L")
                row[f"d{day_idx}_s0_in"] = f"On Leave ({code})"
                continue
            if info["holiday"]:
                row[f"d{day_idx}_s0_in"] = "Holiday"
                continue
            if info["absent"]:
                row[f"d{day_idx}_s0_in"] = "Absent"
                continue

            for slot_idx in range(max_shifts):
                in_t, out_t = (None, None)
                if slot_idx < len(info["slots"]):
                    in_t, out_t = info["slots"][slot_idx]
                if in_t:
                    row[f"d{day_idx}_s{slot_idx}_in"] = in_t.strftime("%-H:%M")
                if out_t:
                    row[f"d{day_idx}_s{slot_idx}_out"] = out_t.strftime("%-H:%M")
        rows.append(row)
    return rows


# ═══════════════════════════════════════════════════════════
#  DATA FETCH  (same logic as the Excel exporter, kept in sync)
# ═══════════════════════════════════════════════════════════
def get_report_data(filters, day_list):
    from_date = day_list[0]
    to_date = day_list[-1]
    department = filters.get("department")
    employee = filters.get("employee")

    emp_filters = {"status": "Active"}
    if department:
        emp_filters["department"] = department
    if employee:
        emp_filters["name"] = employee

    employees = frappe.get_all(
        "Employee",
        filters=emp_filters,
        fields=["name", "employee_name", "default_shift", "department", "holiday_list"],
        order_by="employee_name asc",
    )
    if not employees:
        return [], 1

    emp_ids = [e["name"] for e in employees]

    checkins_raw = frappe.get_all(
        "Employee Checkin",
        filters={
            "employee": ["in", emp_ids],
            "time": [
                "between",
                [
                    datetime.combine(from_date, datetime.min.time()),
                    datetime.combine(to_date, datetime.max.time()),
                ],
            ],
        },
        fields=["employee", "time", "log_type"],
        order_by="employee asc, time asc",
    )

    attendance_raw = frappe.get_all(
        "Attendance",
        filters={
            "employee": ["in", emp_ids],
            "attendance_date": ["between", [from_date, to_date]],
            "docstatus": ["!=", 2],
        },
        fields=["employee", "attendance_date", "status"],
    )

    holiday_list_names = list({e["holiday_list"] for e in employees if e.get("holiday_list")})
    holiday_map = _get_holiday_map(holiday_list_names)

    leave_map = _get_leave_map(emp_ids, from_date, to_date)

    emp_shifts_map = _get_employee_shifts(emp_ids)
    max_shifts = max([len(v) for v in emp_shifts_map.values()] + [1])

    checkins_map = {}
    for c in checkins_raw:
        t = c["time"]
        if isinstance(t, str):
            t = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
        checkins_map.setdefault(c["employee"], {}).setdefault(t.date(), []).append((t, c.get("log_type")))

    attendance_map = {}
    for a in attendance_raw:
        d = a["attendance_date"]
        if isinstance(d, str):
            d = datetime.strptime(d, "%Y-%m-%d").date()
        attendance_map.setdefault(a["employee"], {})[d] = a["status"]

    employees_data = []
    for emp in employees:
        eid = emp["name"]
        shift_slots = emp_shifts_map.get(eid, [])
        emp_holidays = holiday_map.get(emp.get("holiday_list"), {})
        emp_leaves = leave_map.get(eid, {})

        day_values = {}
        for d in day_list:
            checkins = sorted(checkins_map.get(eid, {}).get(d, []), key=lambda x: x[0])
            is_holiday = d in emp_holidays
            leave_type = emp_leaves.get(d)
            is_leave = leave_type is not None
            status = attendance_map.get(eid, {}).get(d, "")

            slot_times = _match_shift_times(checkins, shift_slots, d)

            day_values[d] = {
                # Leave takes precedence over holiday/absent, matching the
                # Personal Attendance Report's status priority.
                "leave": is_leave,
                "leave_type": leave_type,
                "holiday": (not is_leave) and is_holiday and not checkins,
                "absent": (not is_leave) and (not is_holiday) and (status == "Absent") and not checkins,
                "slots": slot_times,
            }

        employees_data.append({
            "employee": eid,
            "employee_name": emp["employee_name"],
            "shift_names": ",".join(s["shift_type"] for s in shift_slots) if shift_slots else (emp.get("default_shift") or ""),
            "day_values": day_values,
        })

    return employees_data, max_shifts


def _match_shift_times(checkins, shift_slots, day_date):
    """
    checkins: list of (datetime, log_type) tuples, sorted by time.
    """
    # No shift slots configured for this employee (or shift-table detection
    # failed) — don't silently drop their checkins. Show first-IN/last-OUT
    # instead, using log_type where available.
    if not shift_slots:
        if not checkins:
            return []

        ins = [t for t, lt in checkins if lt == "IN"]
        outs = [t for t, lt in checkins if lt == "OUT"]

        if ins or outs:
            in_time = min(ins).time() if ins else None
            out_time = max(outs).time() if outs else None
            return [(in_time, out_time)]

        # log_type missing/unrecognized — fall back to earliest/latest punch
        times = [t for t, _ in checkins]
        if len(times) == 1:
            return [(min(times).time(), None)]
        return [(min(times).time(), max(times).time())]

    remaining = list(checkins)
    results = []

    for slot in shift_slots:
        window_start = datetime.combine(day_date, slot["time_in"])
        window_end = datetime.combine(day_date, slot["time_out"])
        if window_end <= window_start:
            window_end += timedelta(days=1)

        buffer = timedelta(hours=SHIFT_MATCH_BUFFER_HOURS)
        matched = [(t, lt) for (t, lt) in remaining if window_start - buffer <= t <= window_end + buffer]
        remaining = [x for x in remaining if x not in matched]

        if matched:
            times = [t for t, _ in matched]
            results.append((min(times).time(), max(times).time()))
        else:
            results.append((None, None))

    return results


def _get_holiday_map(holiday_list_names):
    if not holiday_list_names:
        return {}
    rows = frappe.get_all(
        "Holiday",
        filters={"parent": ["in", holiday_list_names], "parenttype": "Holiday List"},
        fields=["parent", "holiday_date"],
    )
    holiday_map = {}
    for r in rows:
        hd = r["holiday_date"]
        if isinstance(hd, str):
            hd = datetime.strptime(hd, "%Y-%m-%d").date()
        holiday_map.setdefault(r["parent"], {})[hd] = True
    return holiday_map


def _get_leave_map(emp_ids, from_date, to_date):
    """
    Returns {employee: {date: leave_type}} for every day inside an
    approved Leaves Assigment range that overlaps [from_date, to_date].

    NOTE: uses the same "Leaves Assigment" doctype/fields as the Personal
    Attendance Report (employee, from_date, to_date, leave_type). Adjust
    the doctype name here if yours differs.
    """
    if not emp_ids:
        return {}

    rows = frappe.get_all(
        "Leaves Assigment",
        filters={
            "employee": ["in", emp_ids],
            "from_date": ["<=", to_date],
            "to_date": [">=", from_date],
        },
        fields=["employee", "from_date", "to_date", "leave_type"],
    )

    leave_map = {}
    for r in rows:
        lf = r["from_date"]
        lt = r["to_date"]
        if isinstance(lf, str):
            lf = datetime.strptime(lf, "%Y-%m-%d").date()
        if isinstance(lt, str):
            lt = datetime.strptime(lt, "%Y-%m-%d").date()

        # clamp the leave range to the report's date window before expanding
        start = max(lf, from_date)
        end = min(lt, to_date)

        emp_map = leave_map.setdefault(r["employee"], {})
        current = start
        while current <= end:
            emp_map[current] = r.get("leave_type")
            current += timedelta(days=1)

    return leave_map


def _get_employee_shifts(emp_ids):
    cfg = _detect_shift_config()
    result = {}
    for eid in emp_ids:
        try:
            emp_doc = frappe.get_doc("Employee", eid)
        except frappe.DoesNotExistError:
            continue
        rows = emp_doc.get(cfg["table_field"]) or []
        slots = []
        for row in rows:
            time_in = _parse_time_field(row.get(cfg["time_in_field"]))
            time_out = _parse_time_field(row.get(cfg["time_out_field"]))
            if time_in is None or time_out is None:
                continue
            slots.append({
                "shift_type": row.get(cfg["type_field"]) or "Shift",
                "time_in": time_in,
                "time_out": time_out,
            })
        slots.sort(key=lambda s: s["time_in"])
        result[eid] = slots
    return result


def _parse_time_field(value):
    if value is None:
        return None
    if isinstance(value, time_cls):
        return value
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds()) % 86400
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        return time_cls(hour=h, minute=m, second=s)
    if isinstance(value, str):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(value, fmt).time()
            except ValueError:
                continue
    return None


def _parse_date(value):
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _date_range(from_date, to_date):
    days = []
    current = from_date
    while current <= to_date:
        days.append(current)
        current += timedelta(days=1)
    return days