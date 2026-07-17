"""
Monthly Attendance Report — Excel (.xlsx) Generator
=====================================================

Produces a downloadable Excel workbook:

  Row 1 (purple)       : "Employee ID" | "Employee Name" | "Default Shift" | merged
                          day header ("8 Jul") spanning that day's In/Out sub-columns,
                          repeated left-to-right for every day in the date range.
  Row 2 (light purple) : (blank under fixed cols) | "In" | "Out" | "In" | "Out" ...
  Per employee row      : Employee ID | Employee Name | "Shift A,Shift B" | 6:00 | 12:00 | 16:00 | 22:00 ...
                          (plain white, no orange — bold black text for the first 3 columns)

  Day status precedence, matching the Personal Attendance Report and the
  monthly grid page: Leave > Holiday > Absent > normal shift slots.

Requires: openpyxl.
"""

import io
from datetime import datetime, timedelta, date, time as time_cls

import frappe
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


# ═══════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════
SHIFT_MATCH_BUFFER_HOURS = 2

# ── Colours / styling ──
HEADER_FILL    = PatternFill("solid", fgColor="8064A2")   # purple (row 1)
SUBHEADER_FILL = PatternFill("solid", fgColor="A68FC4")   # light purple (row 2, In/Out)
HOLIDAY_FILL   = PatternFill("solid", fgColor="DCE6F1")   # light blue
ABSENT_FILL    = PatternFill("solid", fgColor="FDE9E9")   # light red
LEAVE_FILL     = PatternFill("solid", fgColor="FEF3C7")   # light amber

HEADER_FONT    = Font(bold=True, color="FFFFFF")
SUBHEADER_FONT = Font(bold=True, color="FFFFFF")
EMP_COL_FONT   = Font(bold=True, color="000000")   # plain, no orange
VALUE_FONT     = Font(color="000000")
LEAVE_FONT     = Font(bold=True, color="B45309")

THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center")


# ═══════════════════════════════════════════════════════════
#  WHITELISTED ENTRY POINT
# ═══════════════════════════════════════════════════════════
@frappe.whitelist()
def download_monthly_attendance_excel(from_date, to_date, department=None, employee=None):
    filters = {
        "from_date": from_date,
        "to_date": to_date,
        "department": department,
        "employee": employee,
    }
    workbook_bytes = build_report_workbook(filters)

    frappe.local.response.filename = f"Monthly_Attendance_Report_{from_date}_to_{to_date}.xlsx"
    frappe.local.response.filecontent = workbook_bytes
    frappe.local.response.type = "binary"


def build_report_workbook(filters):
    from_date = _parse_date(filters["from_date"])
    to_date = _parse_date(filters["to_date"])
    day_list = _date_range(from_date, to_date)

    # Reuse the same data-fetch logic as the report page, to stay in sync.
    from rasiin_hr.rasiin_hr.report.monthly_attendance_report.monthly_attendance_report import (
        get_report_data,
        LEAVE_TYPE_CODE_MAP,
    )

    employees_data, max_shifts = get_report_data(filters, day_list)
    wb = _render_workbook(employees_data, day_list, max_shifts, LEAVE_TYPE_CODE_MAP)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


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


# ═══════════════════════════════════════════════════════════
#  WORKBOOK RENDERER
# ═══════════════════════════════════════════════════════════
def _render_workbook(employees_data, day_list, max_shifts, leave_type_code_map=None):
    leave_type_code_map = leave_type_code_map or {}

    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"

    shift_cols_per_day = max_shifts * 2
    FIXED_COLS = 3  # Employee ID, Employee Name, Default Shift

    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 30
    for day_idx in range(len(day_list)):
        start_col = FIXED_COLS + 1 + day_idx * shift_cols_per_day
        for c in range(shift_cols_per_day):
            ws.column_dimensions[get_column_letter(start_col + c)].width = 9

    # ── Row 1: title header, merged vertically across rows 1-2 for fixed cols ──
    for col, label in enumerate(["Employee ID", "Employee Name", "Default Shift"], start=1):
        ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)
        cell = ws.cell(row=1, column=col, value=label)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = BORDER
        ws.cell(row=2, column=col).border = BORDER
        ws.cell(row=2, column=col).fill = HEADER_FILL

    for day_idx, d in enumerate(day_list):
        start_col = FIXED_COLS + 1 + day_idx * shift_cols_per_day
        end_col = start_col + shift_cols_per_day - 1
        ws.merge_cells(start_row=1, start_column=start_col, end_row=1, end_column=end_col)
        top_left = ws.cell(row=1, column=start_col, value=d.strftime("%-d %b"))
        top_left.fill = HEADER_FILL
        top_left.font = HEADER_FONT
        top_left.alignment = CENTER
        for c in range(start_col, end_col + 1):
            ws.cell(row=1, column=c).border = BORDER
            ws.cell(row=1, column=c).fill = HEADER_FILL

    # ── Row 2: In/Out sub-header ──
    for day_idx in range(len(day_list)):
        start_col = FIXED_COLS + 1 + day_idx * shift_cols_per_day
        for slot_idx in range(max_shifts):
            col_in = start_col + slot_idx * 2
            col_out = col_in + 1
            for col, label in ((col_in, "In"), (col_out, "Out")):
                c = ws.cell(row=2, column=col, value=label)
                c.fill = SUBHEADER_FILL
                c.font = SUBHEADER_FONT
                c.alignment = CENTER
                c.border = BORDER

    # ── Row 3+: one row per employee, plain (no orange) ──
    row = 3
    for emp in employees_data:
        for col, val in enumerate(
            [emp["employee"], emp["employee_name"], emp["shift_names"]], start=1
        ):
            cell = ws.cell(row=row, column=col, value=val)
            cell.font = EMP_COL_FONT
            cell.border = BORDER

        for day_idx, d in enumerate(day_list):
            start_col = FIXED_COLS + 1 + day_idx * shift_cols_per_day
            day_info = emp["day_values"][d]

            # Leave takes precedence over holiday/absent, matching the
            # Personal Attendance Report and the monthly grid page.
            if day_info.get("leave"):
                end_col = start_col + shift_cols_per_day - 1
                ws.merge_cells(start_row=row, start_column=start_col, end_row=row, end_column=end_col)
                code = leave_type_code_map.get(day_info.get("leave_type"), "L")
                c = ws.cell(row=row, column=start_col, value=f"On Leave ({code})")
                c.fill = LEAVE_FILL
                c.font = LEAVE_FONT
                c.alignment = CENTER
                for col in range(start_col, end_col + 1):
                    ws.cell(row=row, column=col).border = BORDER
                continue

            if day_info["holiday"]:
                end_col = start_col + shift_cols_per_day - 1
                ws.merge_cells(start_row=row, start_column=start_col, end_row=row, end_column=end_col)
                c = ws.cell(row=row, column=start_col, value="Holiday")
                c.fill = HOLIDAY_FILL
                c.alignment = CENTER
                for col in range(start_col, end_col + 1):
                    ws.cell(row=row, column=col).border = BORDER
                continue

            if day_info["absent"]:
                end_col = start_col + shift_cols_per_day - 1
                ws.merge_cells(start_row=row, start_column=start_col, end_row=row, end_column=end_col)
                c = ws.cell(row=row, column=start_col, value="Absent")
                c.fill = ABSENT_FILL
                c.alignment = CENTER
                for col in range(start_col, end_col + 1):
                    ws.cell(row=row, column=col).border = BORDER
                continue

            for slot_idx in range(max_shifts):
                col_in = start_col + slot_idx * 2
                col_out = col_in + 1
                in_time, out_time = (None, None)
                if slot_idx < len(day_info["slots"]):
                    in_time, out_time = day_info["slots"][slot_idx]

                cell_in = ws.cell(row=row, column=col_in)
                cell_out = ws.cell(row=row, column=col_out)
                cell_in.border = BORDER
                cell_out.border = BORDER
                cell_in.alignment = CENTER
                cell_out.alignment = CENTER

                if in_time:
                    cell_in.value = in_time
                    cell_in.number_format = "H:MM"
                if out_time:
                    cell_out.value = out_time
                    cell_out.number_format = "H:MM"

        row += 1

    # ── Legend row ──
    legend_row = row + 1
    legend_items = [
        ("Holiday", HOLIDAY_FILL),
        ("Absent", ABSENT_FILL),
        ("On Leave (L/C/P/M)", LEAVE_FILL),
    ]
    col = 1
    for label, fill in legend_items:
        c = ws.cell(row=legend_row, column=col, value=label)
        c.fill = fill
        c.alignment = Alignment(horizontal="left", vertical="center")
        col += 2

    ws.freeze_panes = "A3"
    return wb