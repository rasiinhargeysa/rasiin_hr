import frappe
from frappe import _


def execute(filters=None):
    filters = frappe._dict(filters or {})

    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    report_summary = get_report_summary(data)

    message = """
    <style>
        .report-summary .summary-item .summary-value,
        .report-summary .report-summary-item .summary-value,
        .report-summary .summary-item .value,
        .report-summary .report-summary-item .value {
            display: none !important;
        }
        .report-summary .summary-item,
        .report-summary .report-summary-item {
            padding: 0 !important;
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            min-height: 90px !important;
        }
        .report-summary,
        .summary-wrapper {
            display: flex !important;
            flex-wrap: wrap !important;
            gap: 0px !important;
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            padding: 0px 0 !important;
            grid-template-columns: repeat(7, 1fr) !important;  /* 👈 7 per row */
        }
    </style>
    """

    return columns, data, message, chart, report_summary


def get_columns():
    return [
        {"label": _("Employee ID"),      "fieldname": "employee",         "fieldtype": "Link",     "options": "Employee",         "width": 120},
        {"label": _("Employee Name"),    "fieldname": "employee_name",    "fieldtype": "Data",                                    "width": 180},
        {"label": _("Company"),          "fieldname": "company",          "fieldtype": "Link",     "options": "Company",          "width": 160},
        {"label": _("Department"),       "fieldname": "department",       "fieldtype": "Link",     "options": "Department",       "width": 140},
        {"label": _("Designation"),      "fieldname": "designation",      "fieldtype": "Link",     "options": "Designation",      "width": 140},
        {"label": _("Employment Type"),  "fieldname": "employment_type",  "fieldtype": "Link",     "options": "Employment Type",  "width": 130},
        {"label": _("Branch"),           "fieldname": "branch",           "fieldtype": "Link",     "options": "Branch",           "width": 120},
        {"label": _("Status"),           "fieldname": "status",           "fieldtype": "Data",                                    "width": 100},
        {"label": _("Date of Joining"),  "fieldname": "date_of_joining",  "fieldtype": "Date",                                    "width": 120},
        {"label": _("Gender"),           "fieldname": "gender",           "fieldtype": "Data",                                    "width": 100},
        {"label": _("Mobile Number"),    "fieldname": "cell_number",      "fieldtype": "Data",                                    "width": 130},
        {"label": _("Salary Mode"),      "fieldname": "salary_mode",      "fieldtype": "Data",                                    "width": 120},
        {"label": _("Bank A/C No"),      "fieldname": "bank_ac_no",       "fieldtype": "Data",                                    "width": 140},
        {"label": _("Base Salary"),      "fieldname": "base_salary",      "fieldtype": "Currency",                                "width": 140},
        {"label": _("Salary Structure"), "fieldname": "salary_structure", "fieldtype": "Link",     "options": "Salary Structure", "width": 160},
    ]


def get_data(filters):
    conditions = {}
    where = []

    if filters.get("company"):
        where.append("e.company = %(company)s")
        conditions["company"] = filters.company

    if filters.get("department"):
        where.append("e.department = %(department)s")
        conditions["department"] = filters.department

    if filters.get("designation"):
        where.append("e.designation = %(designation)s")
        conditions["designation"] = filters.designation

    if filters.get("employment_type"):
        where.append("e.employment_type = %(employment_type)s")
        conditions["employment_type"] = filters.employment_type

    if filters.get("status"):
        where.append("e.status = %(status)s")
        conditions["status"] = filters.status

    where_clause = "WHERE " + " AND ".join(where) if where else ""

    query = f"""
        SELECT
            e.name AS employee,
            e.employee_name,
            e.company,
            e.department,
            e.designation,
            e.employment_type,
            e.branch,
            e.status,
            e.date_of_joining,
            e.gender,
            e.cell_number,
            e.salary_mode,
            e.bank_ac_no,
            IFNULL(ssa.base, 0) AS base_salary,
            IFNULL(ssa.salary_structure, '') AS salary_structure
        FROM `tabEmployee` e
        LEFT JOIN (
            SELECT
                employee,
                salary_structure,
                base,
                MAX(from_date) AS latest_date
            FROM `tabSalary Structure Assignment`
            WHERE docstatus = 1
            GROUP BY employee
        ) ssa ON e.name = ssa.employee
        {where_clause}
        ORDER BY e.employee_name ASC
    """

    return frappe.db.sql(query, conditions, as_dict=True)


def get_chart(data):
    """Bar + line axis-mixed chart: employee count (bar) and salary cost (line) per department."""
    if not data:
        return None

    dept_headcount = {}
    dept_salary    = {}

    for row in data:
        dept = row.get("department") or _("Unassigned")
        dept_headcount[dept] = dept_headcount.get(dept, 0) + 1
        dept_salary[dept]    = dept_salary.get(dept, 0.0) + (row.get("base_salary") or 0.0)

    sorted_depts   = sorted(dept_headcount, key=lambda d: dept_headcount[d], reverse=True)
    headcount_vals = [dept_headcount[d] for d in sorted_depts]
    salary_vals    = [round(dept_salary[d], 2) for d in sorted_depts]

    return {
        "data": {
            "labels": sorted_depts,
            "datasets": [
                {"name": _("👥 Employee Count"),    "chartType": "bar",  "values": headcount_vals},
                {"name": _("💰 Total Salary Cost"), "chartType": "line", "values": salary_vals},
            ],
        },
        "type": "axis-mixed",
        "height": 320,
        "colors": ["#5e64ff", "#ff5858"],
        "axisOptions": {"xIsSeries": False, "shortenYAxisNumbers": True},
        "barOptions":  {"spaceRatio": 0.4},
        "lineOptions": {"dotSize": 6, "hideLine": False, "heatline": False, "regionFill": 1},
    }


# ── Card style helpers ────────────────────────────────────────────────────────

# Palette: (top_border_color, value_color)
CARD_STYLE = {
    "blue":   ("#1e40af", "#1e40af"),
    "green":  ("#15803d", "#15803d"),
    "orange": ("#d97706", "#d97706"),
    "purple": ("#6d28d9", "#6d28d9"),
    "teal":   ("#0f766e", "#0f766e"),
    "gray":   ("#64748b", "#64748b"),
    "pink":   ("#be185d", "#be185d"),
    "cyan":   ("#0369a1", "#0369a1"),
    "red":    ("#b91c1c", "#b91c1c"),
}

GENDER_ICON = {"male": "👨", "female": "👩"}
EMPTYPE_ICON = {
    "full-time": "🏢", "part-time": "🕐",
    "contract": "📝", "intern": "🎓", "probation": "🔍",
}


def make_card_label(icon, text, border_color, value_color, value, datatype):
    """
    Card style matching the sample:
    - White background
    - Colored top border
    - Icon + label text on top row (small, gray)
    - Large bold colored value below
    """
    if datatype == "Currency":
        currency = frappe.defaults.get_global_default("currency") or ""
        formatted = frappe.utils.fmt_money(value, currency=currency)
    else:
        formatted = str(int(value)) if isinstance(value, (int, float)) and float(value) == int(float(value)) else str(value)

    return (
        f'<div style="'
        f'display:flex;'
        f'flex-direction:column;'
        f'justify-content:space-between;'
        f'background:#ffffff;'
        f'border:1px solid #e5e7eb;'
        f'border-top:3px solid {border_color};'
        f'border-radius:8px;'
        f'padding:16px 18px;'
        f'min-height:50px;'
        f'min-width:160px;'
        f'box-shadow:0 1px 3px rgba(0,0,0,.06);'
        f'font-family:Segoe UI,sans-serif;'
        f'">'

        f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:10px;">'
        f'<span style="font-size:16px;line-height:1">{icon}</span>'
        f'<span style="font-size:12px;font-weight:500;color:#6b7280;'
        f'line-height:1.3">{text}</span>'
        f'</div>'

        f'<span style="font-size:26px;font-weight:700;color:{value_color};'
        f'line-height:1;letter-spacing:-0.5px">{formatted}</span>'

        f'</div>'
    )


def summary_card(icon, text, value, datatype, style_key):
    border_color, value_color = CARD_STYLE.get(style_key, CARD_STYLE["gray"])
    return {
        "value":     value,
        "label":     make_card_label(icon, text, border_color, value_color, value, datatype),
        "datatype":  datatype,
        "indicator": style_key.capitalize(),
    }


# ── Main summary builder ──────────────────────────────────────────────────────

def get_report_summary(data):
    if not data:
        return []

    total_employees   = len(data)
    total_salary_cost = sum(row.get("base_salary") or 0 for row in data)
    avg_salary        = total_salary_cost / total_employees if total_employees else 0
    active_count      = sum(1 for row in data if row.get("status") == "Active")
    inactive_count    = total_employees - active_count

    gender_count          = {}
    employment_type_count = {}

    for row in data:
        gender   = row.get("gender")          or _("Not Specified")
        emp_type = row.get("employment_type") or _("Not Specified")
        gender_count[gender]            = gender_count.get(gender, 0) + 1
        employment_type_count[emp_type] = employment_type_count.get(emp_type, 0) + 1

    summary = [
        summary_card("👥", _("Total Employees"),   total_employees,   "Int",      "blue"),
        summary_card("✅", _("Active Employees"),   active_count,      "Int",      "green"),
        summary_card("⏸", _("Inactive Employees"), inactive_count,    "Int",      "orange"),
        summary_card("💰", _("Total Salary Cost"),  total_salary_cost, "Currency", "teal"),
        summary_card("📊", _("Average Salary"),     avg_salary,        "Currency", "purple"),
    ]

    for gender, count in sorted(gender_count.items(), key=lambda x: -x[1]):
        icon = GENDER_ICON.get(gender.lower(), "👤")
        summary.append(summary_card(icon, _("Gender: {0}").format(gender), count, "Int", "pink"))

    return summary