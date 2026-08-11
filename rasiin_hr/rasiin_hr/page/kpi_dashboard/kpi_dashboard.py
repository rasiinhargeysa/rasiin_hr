import frappe
from frappe.utils import flt, getdate, nowdate
from collections import defaultdict


# ---- Child table option orders (used for consistent chip / summary ordering) ----
STATUS_ORDER = ["Not Started", "Pending", "In progress", "completed", "Overdue"]
PRIORITY_ORDER = ["High", "Middle", "Low"]


@frappe.whitelist()
def get_kpi_dashboard_data(from_date=None, to_date=None, department=None,
                           status=None, priority=None):
    frappe.only_for("System Manager")

    filters = {}
    if from_date and to_date:
        filters["date"] = ["between", [from_date, to_date]]
    if department:
        filters["department"] = department

    records = frappe.get_all(
        "Performance KPIs",
        filters=filters,
        fields=["name", "date"],
        order_by="date desc"
    )

    items = []
    if records:
        items = frappe.get_all(
            "Performance KPI Items",
            filters={"parent": ["in", [r.name for r in records]]},
            fields=["parent", "idx", "key_result_areas", "key_performance_indicator",
                    "weight_of_kpis", "target", "actual", "score", "final",
                    "status", "priority", "ending_date"],
            order_by="parent, idx"
        )

    # Optional child-level filters. Applied before any aggregation so that every
    # card, bar and table row below stays consistent with the visible rows.
    if status:
        items = [i for i in items if (i.status or "") == status]
    if priority:
        items = [i for i in items if (i.priority or "") == priority]

    total_kpi_items = len(items)

    avg_score = flt(
        sum(flt(i.score) for i in items) / total_kpi_items, 2
    ) if total_kpi_items else 0

    # "Overall avg final score" card always reflects average Score across all rows,
    # regardless of whether a department filter is applied.
    overall_avg = avg_score

    if department:
        total_final = flt(sum(flt(i.final) for i in items), 2)
        total_final_label = "Total"
    else:
        total_final = avg_score
        total_final_label = "Average Score"

    # KRA breakdown is based on Score (0-100) so it shares the same colour
    # thresholds as the detail table row highlighting.
    by_kra = defaultdict(list)
    for i in items:
        if i.key_result_areas:
            by_kra[i.key_result_areas].append(flt(i.score))
    kra_avg = [{"kra": k, "avg": flt(sum(v) / len(v), 2)} for k, v in by_kra.items()]
    kra_avg.sort(key=lambda x: x["avg"], reverse=True)

    weak_areas = len([i for i in items if flt(i.score) < 60])

    # ---- Status / Priority breakdown ----
    status_counts = defaultdict(int)
    priority_counts = defaultdict(int)
    for i in items:
        status_counts[i.status or "Not Set"] += 1
        priority_counts[i.priority or "Not Set"] += 1

    def _summary(counts, order):
        out = [{"label": k, "count": counts.get(k, 0)} for k in order if counts.get(k)]
        out += [{"label": k, "count": v} for k, v in counts.items() if k not in order]
        return out

    status_summary = _summary(status_counts, STATUS_ORDER)
    priority_summary = _summary(priority_counts, PRIORITY_ORDER)

    # ---- Deadline metrics ----
    # NOTE: the child table has no "date completed" field, so "on time" is
    # inferred as: status is completed AND the ending date has not passed.
    # Add a `completed_on` Date field to the child table for an exact figure.
    today = getdate(nowdate())

    def _is_completed(item):
        return (item.status or "") == "completed"

    def _is_late(item):
        if _is_completed(item):
            return False
        if not item.ending_date:
            return False
        return getdate(item.ending_date) < today

    finished_count = 0
    finished_on_time = 0
    finished_late = 0
    overdue_count = 0
    upcoming_count = 0
    no_deadline_count = 0

    for i in items:
        if not i.ending_date:
            no_deadline_count += 1

        if _is_completed(i):
            finished_count += 1
            if not i.ending_date or getdate(i.ending_date) >= today:
                finished_on_time += 1
            else:
                finished_late += 1
            continue

        if (i.status or "") == "Overdue" or _is_late(i):
            overdue_count += 1
        elif i.ending_date and getdate(i.ending_date) >= today:
            upcoming_count += 1

    completion_pct = flt(finished_count * 100.0 / total_kpi_items, 1) if total_kpi_items else 0
    on_time_pct = flt(finished_on_time * 100.0 / finished_count, 1) if finished_count else 0

    deadline_summary = [
        {"label": "Finished on time", "count": finished_on_time, "cls": "dl-ontime"},
        {"label": "Finished late", "count": finished_late, "cls": "dl-late"},
        {"label": "Overdue", "count": overdue_count, "cls": "dl-overdue"},
        {"label": "Due upcoming", "count": upcoming_count, "cls": "dl-upcoming"},
        {"label": "No deadline set", "count": no_deadline_count, "cls": "dl-none"},
    ]

    detail_rows = []
    for idx, i in enumerate(items, start=1):
        detail_rows.append({
            "no": idx,
            "kra": i.key_result_areas,
            "kpi": i.key_performance_indicator,
            "weight": flt(i.weight_of_kpis),
            "target": i.target,
            "actual": i.actual,
            "score": flt(i.score),
            "final": flt(i.final),
            "status": i.status or "",
            "priority": i.priority or "",
            "ending_date": str(i.ending_date) if i.ending_date else "",
            "is_late": _is_late(i)
        })

    return {
        "cards": {
            "total_kpi_items": total_kpi_items,
            "overall_avg": overall_avg,
            "weak_areas": weak_areas,
            "completion_pct": completion_pct,
            "finished_count": finished_count,
            "finished_on_time": finished_on_time,
            "finished_late": finished_late,
            "on_time_pct": on_time_pct,
            "overdue_count": overdue_count,
            "upcoming_count": upcoming_count,
        },
        "kra_avg": kra_avg,
        "status_summary": status_summary,
        "priority_summary": priority_summary,
        "deadline_summary": deadline_summary,
        "detail_rows": detail_rows,
        "total_final": total_final,
        "total_final_label": total_final_label
    }


@frappe.whitelist()
def export_kpi_excel(from_date=None, to_date=None, department=None,
                     status=None, priority=None):
    frappe.only_for("System Manager")
    from frappe.utils.xlsxutils import make_xlsx

    data = get_kpi_dashboard_data(from_date, to_date, department, status, priority)
    rows = [["No.", "Key Result Area", "Key Performance Indicator", "Weight",
             "Target", "Actual", "Ending Date", "Status", "Priority",
             "Score", "Final Score"]]
    for r in data["detail_rows"]:
        rows.append([r["no"], r["kra"], r["kpi"], r["weight"],
                     r["target"], r["actual"], r["ending_date"],
                     r["status"], r["priority"], r["score"], r["final"]])
    rows.append(["", "", "", "", "", "", "", "", "",
                 data["total_final_label"], data["total_final"]])

    # Deadline summary block appended under the detail rows
    c = data["cards"]
    rows.append([])
    rows.append(["Deadline summary"])
    rows.append(["Finished", c["finished_count"]])
    rows.append(["Finished on time", c["finished_on_time"]])
    rows.append(["Finished late", c["finished_late"]])
    rows.append(["On-time rate (%)", c["on_time_pct"]])
    rows.append(["Overdue", c["overdue_count"]])
    rows.append(["Due upcoming", c["upcoming_count"]])

    xlsx_file = make_xlsx(rows, "KPI Report")

    frappe.response["filename"] = "kpi_report.xlsx"
    frappe.response["filecontent"] = xlsx_file.getvalue()
    frappe.response["type"] = "binary"