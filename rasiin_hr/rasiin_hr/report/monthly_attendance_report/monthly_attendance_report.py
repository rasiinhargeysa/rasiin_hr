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

DUPLICATE PUNCHES
-----------------
Repeat swipes on the same reader used to surface as an In and an Out at the
same HH:MM (and a lone punch became In == Out, because the slot's earliest
and latest punch were the same record). Swipes within
PUNCH_DEDUPE_WINDOW_MINUTES of each other are collapsed into one punch, and
a slot never reports an Out that isn't strictly later than its In — the
punch is kept as the In and the Out is left blank.

NO PUNCH IS EVER DROPPED
------------------------
Matching runs in two passes:

  1. Window pass — each punch is placed in the slot whose match window it
     falls inside. Windows are open-ended at the edges of the day (see
     _build_slot_windows) and clamped at the midpoint between adjacent
     slots.
  2. Nearest-slot pass — any punch that landed in NO window is attached to
     whichever slot it sits closest to, rather than being discarded.

The second pass is the important one. Previously a punch outside every
window vanished from the report with no trace, so a shift whose configured
time_out didn't match reality silently lost its check-out. Now the worst
case is a punch shown against a slightly odd slot, which is visible and
fixable, instead of a blank cell that looks like the employee never left.
"""

from datetime import datetime, timedelta, date, time as time_cls

import frappe

SHIFT_CHILD_TABLE_FIELDNAME = "shift_type_slot"
SHIFT_TYPE_SUBFIELD = "shift_type"
SHIFT_TIME_IN_SUBFIELD = "time_in"
SHIFT_TIME_OUT_SUBFIELD = "time_out"

# Set to True to skip child-table auto-detection and always use the four
# constants above. The Personal Attendance Report hardcodes
# `shift_type_slot`, so forcing it here keeps both reports reading the exact
# same source — worth doing if debug_shift_detection() returns anything
# unexpected.
FORCE_SHIFT_CONFIG = True

# Marker for which matching rule this file implements. The boundary between
# two consecutive shift slots is the earliest moment a check-in for the
# LATER slot is accepted, i.e.
#     next_slot.time_in - next_slot.begin_check_in_before_shift_start_time
# This replaces the old fixed-hours buffer, which cut off late check-outs,
# and the midpoint rule, which flipped a late departure into the next
# slot's check-in depending on how long the break happened to be.
BOUNDARY_RULE = "check_in_grace"

# How close two swipes have to be before they're treated as the same punch.
# Raise this (e.g. 5) if your readers commonly register repeats.
PUNCH_DEDUPE_WINDOW_MINUTES = 1

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
_SHIFT_TYPE_BUFFER_CACHE = {}


def _detect_shift_config():
    """
    Resolves which Employee child table holds shift slots.

    With FORCE_SHIFT_CONFIG on, the module constants are used as-is. With it
    off, the Employee doctype's child tables are scanned for a shift link
    plus a pair of Time fields — handy if your install renamed things, but
    it can guess wrong, which is why forcing is the default.
    """
    if _SHIFT_CONFIG_CACHE:
        return _SHIFT_CONFIG_CACHE

    if FORCE_SHIFT_CONFIG:
        _SHIFT_CONFIG_CACHE.update({
            "table_field": SHIFT_CHILD_TABLE_FIELDNAME,
            "type_field": SHIFT_TYPE_SUBFIELD,
            "time_in_field": SHIFT_TIME_IN_SUBFIELD,
            "time_out_field": SHIFT_TIME_OUT_SUBFIELD,
            "source": "forced",
        })
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
                "source": "detected",
            })
            return _SHIFT_CONFIG_CACHE

    # Nothing auto-detected — fall back to the constants
    _SHIFT_CONFIG_CACHE.update({
        "table_field": SHIFT_CHILD_TABLE_FIELDNAME,
        "type_field": SHIFT_TYPE_SUBFIELD,
        "time_in_field": SHIFT_TIME_IN_SUBFIELD,
        "time_out_field": SHIFT_TIME_OUT_SUBFIELD,
        "source": "fallback",
    })
    return _SHIFT_CONFIG_CACHE


def _get_shift_type_grace(shift_type):
    """
    Returns (before, after) timedeltas straight from the Shift Type:
      before = begin_check_in_before_shift_start_time
      after  = allow_check_out_after_shift_end_time

    These are used RAW, with no minimum floor. A floor would push the
    slot boundary earlier and undo the whole point of the rule — with a
    2h floor and a 16:00 afternoon shift the boundary lands at 14:00,
    which is exactly where a 14:04 departure gets misread as an early
    afternoon check-in.
    """
    if not shift_type:
        return timedelta(0), timedelta(0)

    if shift_type in _SHIFT_TYPE_BUFFER_CACHE:
        return _SHIFT_TYPE_BUFFER_CACHE[shift_type]

    before = after = timedelta(0)
    try:
        row = frappe.db.get_value(
            "Shift Type",
            shift_type,
            [
                "begin_check_in_before_shift_start_time",
                "allow_check_out_after_shift_end_time",
            ],
            as_dict=True,
        )
        if row:
            before = timedelta(minutes=row.get("begin_check_in_before_shift_start_time") or 0)
            after = timedelta(minutes=row.get("allow_check_out_after_shift_end_time") or 0)
    except Exception:
        # Shift Type missing, or fields not present in this install
        pass

    _SHIFT_TYPE_BUFFER_CACHE[shift_type] = (before, after)
    return before, after


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


@frappe.whitelist()
def debug_slot_windows(employee, day):
    """
    Diagnostic helper: shows each shift slot's match window for one
    employee on one date, plus that day's punches and where each one landed.

      /api/method/<app>.<...>.debug_slot_windows?employee=EMP-00026&day=2026-08-01

    `matched_slot` is the slot the window pass chose; `assigned_slot` is
    where the punch actually ended up after the nearest-slot fallback. A
    punch with matched_slot=None but an assigned_slot was rescued by the
    fallback — a sign that slot's configured times don't match reality.
    """
    day_date = _parse_date(day)
    slots = _get_employee_shifts([employee]).get(employee, [])
    windows = _build_slot_windows(slots, day_date)

    raw = frappe.get_all(
        "Employee Checkin",
        filters={
            "employee": employee,
            "time": [
                "between",
                [
                    datetime.combine(day_date, datetime.min.time()),
                    datetime.combine(day_date + timedelta(days=1), datetime.max.time()),
                ],
            ],
        },
        fields=["time", "log_type"],
        order_by="time asc",
    )
    punches = []
    for c in raw:
        t = c["time"]
        if isinstance(t, str):
            t = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
        punches.append((t, c.get("log_type")))

    deduped = _dedupe_checkins(punches)
    assignment = _assign_punches(deduped, slots, day_date)

    punch_report = []
    for t, lt in deduped:
        matched = None
        for i, (ws_, we_) in enumerate(windows):
            if ws_ <= t <= we_:
                matched = i
                break
        assigned = next(
            (i for i, times in enumerate(assignment) if t in times), None
        )
        punch_report.append({
            "time": str(t),
            "log_type": lt,
            "matched_slot": matched,
            "assigned_slot": assigned,
            "rescued_by_fallback": matched is None and assigned is not None,
        })

    return {
        "shift_config": _detect_shift_config(),
        "boundary_rule": BOUNDARY_RULE,
        "slots": [
            {
                "shift_type": s["shift_type"],
                "time_in": str(s["time_in"]),
                "time_out": str(s["time_out"]),
                "window_start": str(windows[i][0]),
                "window_end": str(windows[i][1]),
            }
            for i, s in enumerate(slots)
        ],
        "punches_raw": len(punches),
        "punches": punch_report,
        "result": [
            (str(a) if a else None, str(b) if b else None)
            for a, b in _match_shift_times(punches, slots, day_date)
        ],
    }


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

    # Pull one extra day at each end so a late check-out that rolls past
    # midnight (or an early pre-shift punch) is still available for matching.
    checkins_raw = frappe.get_all(
        "Employee Checkin",
        filters={
            "employee": ["in", emp_ids],
            "time": [
                "between",
                [
                    datetime.combine(from_date - timedelta(days=1), datetime.min.time()),
                    datetime.combine(to_date + timedelta(days=1), datetime.max.time()),
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
        emp_checkins = checkins_map.get(eid, {})

        day_values = {}
        for d in day_list:
            same_day_checkins = emp_checkins.get(d, [])

            # This day's punches, plus the following morning's, so a
            # check-out after midnight can still be matched. Next-day
            # punches are only worth pooling when a slot actually runs
            # overnight, otherwise they'd be dragged in by the fallback.
            pool = list(same_day_checkins)
            if _has_overnight_slot(shift_slots):
                pool += emp_checkins.get(d + timedelta(days=1), [])
            checkins = sorted(pool, key=lambda x: x[0])

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
                # Holiday/Absent look at same-day punches only — a spillover
                # punch from the next morning shouldn't mark the day worked.
                "holiday": (not is_leave) and is_holiday and not same_day_checkins,
                "absent": (not is_leave) and (not is_holiday) and (status == "Absent") and not same_day_checkins,
                "slots": slot_times,
            }

        employees_data.append({
            "employee": eid,
            "employee_name": emp["employee_name"],
            "shift_names": ",".join(s["shift_type"] for s in shift_slots) if shift_slots else (emp.get("default_shift") or ""),
            "day_values": day_values,
        })

    return employees_data, max_shifts


# ═══════════════════════════════════════════════════════════
#  PUNCH MATCHING
# ═══════════════════════════════════════════════════════════
def _dedupe_checkins(checkins, window_minutes=PUNCH_DEDUPE_WINDOW_MINUTES):
    """
    Collapse repeat swipes registered within `window_minutes` of each other
    into a single punch, keeping the earliest.

    checkins: list of (datetime, log_type) tuples.
    """
    window = timedelta(minutes=window_minutes)
    kept = []
    for entry in sorted(checkins, key=lambda x: x[0]):
        if kept and (entry[0] - kept[-1][0]) <= window:
            continue
        kept.append(entry)
    return kept


def _in_out_pair(times, slot_bounds=None):
    """
    Build an (in, out) tuple of time objects from a list of datetimes,
    never returning the same value twice.

    A lone punch — or a double swipe that survived deduping — is recorded
    against one side only. Which side depends on whether it sits nearer the
    slot's own start or its end, matching the Personal Attendance Report:
    a single 22:29 punch in a 16:00-20:00 slot is a check-out with a
    missing check-in, not a check-in at 22:29.
    """
    if not times:
        return (None, None)

    first = min(times)
    last = max(times)

    if last <= first:
        if slot_bounds:
            start, end = slot_bounds
            if abs((first - end).total_seconds()) < abs((first - start).total_seconds()):
                return (None, first.time())
        return (first.time(), None)

    return (first.time(), last.time())


def _has_overnight_slot(shift_slots):
    """True if any slot's time_out is at or before its time_in."""
    return any(s["time_out"] <= s["time_in"] for s in shift_slots)


def _slot_bounds(shift_slots, day_date):
    """Each slot's own start/end as datetimes, overnight slots rolled forward."""
    bounds = []
    for slot in shift_slots:
        start = datetime.combine(day_date, slot["time_in"])
        end = datetime.combine(day_date, slot["time_out"])
        if end <= start:
            # overnight slot, e.g. 22:00 -> 07:00
            end += timedelta(days=1)
        bounds.append((start, end))
    return bounds


def _build_slot_windows(shift_slots, day_date):
    """
    Returns a (window_start, window_end) datetime pair per shift slot.

    Windows tile the whole day with no gaps:

      * the FIRST slot's window opens at midnight
      * the LAST slot's window runs to end of day, or later if its
        allow_check_out_after grace reaches past midnight
      * BETWEEN two slots the boundary is the earliest moment a check-in
        for the later slot is accepted:
            next.time_in - next.begin_check_in_before
        clamped so it never falls before the earlier slot's own time_out

    Because the windows tile the day, a punch can't fall through the
    cracks, and a late departure stays with the shift it belongs to
    instead of flipping to the next one.
    """
    if not shift_slots:
        return []

    bounds = _slot_bounds(shift_slots, day_date)
    day_start = datetime.combine(day_date, time_cls.min)
    day_end = datetime.combine(day_date, time_cls.max)
    last_idx = len(bounds) - 1

    # boundaries[i] separates slot i from slot i+1
    boundaries = []
    for i in range(last_idx):
        nxt_before, _ = _get_shift_type_grace(shift_slots[i + 1].get("shift_type"))
        boundary = bounds[i + 1][0] - nxt_before

        prev_end = bounds[i][1]
        if boundary < prev_end:
            boundary = prev_end

        boundaries.append(boundary)

    windows = []
    for i in range(len(bounds)):
        win_start = day_start if i == 0 else boundaries[i - 1]

        if i == last_idx:
            _, after = _get_shift_type_grace(shift_slots[i].get("shift_type"))
            win_end = max(bounds[i][1] + after, day_end)
        else:
            # a punch exactly on the boundary is a check-in for the later slot
            win_end = boundaries[i] - timedelta(seconds=1)

        windows.append((win_start, win_end))

    return windows


def _assign_punches(checkins, shift_slots, day_date):
    """
    Distribute punches across slots. Returns a list of datetime lists,
    one per slot.

    Pass 1 places each punch in the slot whose window contains it.
    Pass 2 attaches anything left over to the nearest slot by clock
    distance, so a punch is never silently discarded because the
    configured shift times don't line up with what people actually do.
    """
    per_slot = [[] for _ in shift_slots]
    if not shift_slots:
        return per_slot

    windows = _build_slot_windows(shift_slots, day_date)
    bounds = _slot_bounds(shift_slots, day_date)
    leftovers = []

    for t, _lt in checkins:
        for i, (win_start, win_end) in enumerate(windows):
            if win_start <= t <= win_end:
                per_slot[i].append(t)
                break
        else:
            # no window matched this punch
            leftovers.append(t)

    for t in leftovers:
        best_i, best_dist = 0, None
        for i, (start, end) in enumerate(bounds):
            if t < start:
                dist = (start - t).total_seconds()
            elif t > end:
                dist = (t - end).total_seconds()
            else:
                dist = 0
            if best_dist is None or dist < best_dist:
                best_i, best_dist = i, dist
        per_slot[best_i].append(t)

    return per_slot


def _match_shift_times(checkins, shift_slots, day_date):
    """
    checkins: list of (datetime, log_type) tuples.
    Returns [(in_time, out_time), ...] aligned with shift_slots.
    """
    # Collapse double swipes first, so a repeat punch can't become an Out
    # at the same time as its In.
    checkins = _dedupe_checkins(checkins)

    # No shift slots configured for this employee — don't drop their
    # checkins. Show first-IN/last-OUT instead, using log_type where
    # available.
    if not shift_slots:
        same_day = [(t, lt) for (t, lt) in checkins if t.date() == day_date]
        if not same_day:
            return []

        ins = [t for t, lt in same_day if lt == "IN"]
        outs = [t for t, lt in same_day if lt == "OUT"]

        if ins or outs:
            in_time = min(ins) if ins else None
            out_time = max(outs) if outs else None
            # An Out that isn't strictly later than the In is a duplicate
            # swipe, not a zero-length work period — keep the In only.
            if in_time and out_time and out_time <= in_time:
                out_time = None
            return [(
                in_time.time() if in_time else None,
                out_time.time() if out_time else None,
            )]

        # log_type missing/unrecognized — fall back to earliest/latest punch
        return [_in_out_pair([t for t, _ in same_day])]

    per_slot = _assign_punches(checkins, shift_slots, day_date)
    bounds = _slot_bounds(shift_slots, day_date)
    return [_in_out_pair(times, bounds[i]) for i, times in enumerate(per_slot)]


# ═══════════════════════════════════════════════════════════
#  LOOKUPS
# ═══════════════════════════════════════════════════════════
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