// Monthly Attendance Report — Filters
// =====================================
// This file defines the filter bar shown above the report in ERPNext.

frappe.query_reports["Monthly attendance report"] = {

filters: [
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.month_start(),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.month_end(),
            reqd: 1,
        },
        {
            fieldname: "department",
            label: __("Department"),
            fieldtype: "Link",
            options: "Department",
            reqd: 0,
        },
        {
            fieldname: "employee",
            label: __("Employee"),
            fieldtype: "Link",
            options: "Employee",
            reqd: 0,
            get_query: function () {
                const dept = frappe.query_report.get_filter_value("department");
                return dept ? { filters: { department: dept } } : {};
            },
        },
    ],
 
    // ── Cell coloring ─────────────────────────────────
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;
 
        const fn = column.fieldname;
 
        // // Fixed employee columns — orange, matching the Excel label row
        // if (fn === "employee" || fn === "employee_name" || fn === "shift_names") {
        //     return `<div style="
        //         background:#F79646;color:#fff;font-weight:600;
        //         margin:-4px -10px;padding:4px 10px;">${data[fn] || ""}</div>`;
        // }
 
        // Per-shift In/Out day columns
        if (fn && /^d\d+_s\d+_(in|out)$/.test(fn)) {
            const raw = data[fn];
            if (!raw) return "";
 
            if (raw === "Holiday") {
                return `<span style="
                    background:#DCE6F1;color:#2563eb;
                    padding:2px 8px;border-radius:4px;
                    font-size:11px;font-weight:600;">Holiday</span>`;
            }
            if (raw === "Absent") {
                return `<span style="
                    background:#FDE9E9;color:#e11d48;
                    padding:2px 8px;border-radius:4px;
                    font-size:11px;font-weight:600;">Absent</span>`;
            }
 
            return `<span style="font-family:monospace;">${raw}</span>`;
        }
 
        return value;
    },
 
    // ── Header + row styling to match the purple/orange mock-up ──
    onload: function (report) {
        if (document.getElementById("attendance-report-style")) return;
 
        const style = document.createElement("style");
        style.id = "attendance-report-style";
        style.innerHTML = `
            /* Purple header row, like the Excel export */
            .datatable .dt-header .dt-cell--header,
            .datatable .dt-header .dt-cell--header .dt-cell__content {
                background: #8064A2 !important;
                color: #fff !important;
                font-weight: 600 !important;
            }
        `;
        document.head.appendChild(style);
    },
};
 