const FULL_METHOD_PATH = "rasiin_hr.rasiin_hr.report.monthly_attendance_report.monthly_attendance_report.get_attendance_grid";
const EXCEL_METHOD_PATH = "rasiin_hr.rasiin_hr.report.monthly_attendance_report.monthly_attendance_excel.download_monthly_attendance_excel";


frappe.pages["monthly-attendance-g"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: "Monthly Attendance Report",
        single_column: true,
    });
 
    const grid = new AttendanceGrid(page);
 
    page.set_primary_action("Print", () => grid.print_report(), "printer");
    page.add_inner_button("Export to Excel", () => grid.export_excel());
};
 
class AttendanceGrid {
    constructor(page) {
        this.page = page;
        this.build_filters();
        this.build_table_container();
        this.fetch_and_render();
    }
 
    build_filters() {
        this.from_date = this.page.add_field({
            fieldname: "from_date",
            label: "From Date",
            fieldtype: "Date",
            default: frappe.datetime.month_start(),
            change: () => this.fetch_and_render(),
        });
        this.to_date = this.page.add_field({
            fieldname: "to_date",
            label: "To Date",
            fieldtype: "Date",
            default: frappe.datetime.month_end(),
            change: () => this.fetch_and_render(),
        });
        this.department = this.page.add_field({
            fieldname: "department",
            label: "Department",
            fieldtype: "Link",
            options: "Department",
            change: () => this.fetch_and_render(),
        });
        this.employee = this.page.add_field({
            fieldname: "employee",
            label: "Employee",
            fieldtype: "Link",
            options: "Employee",
            change: () => this.fetch_and_render(),
        });
    }
 
    build_table_container() {
        this.$wrapper = $(`<div class="attendance-grid-wrapper" style="overflow-x:auto;"></div>`);
        this.page.main.append(this.$wrapper);
        this.inject_styles();
    }
 
    inject_styles() {
        if (document.getElementById("attendance-grid-style")) return;
        const style = document.createElement("style");
        style.id = "attendance-grid-style";
        style.innerHTML = `
            .attendance-grid { border-collapse: collapse; width: max-content; min-width: 100%; }
            .attendance-grid th, .attendance-grid td {
                border: 1px solid #d1d5db;
                padding: 4px 8px;
                font-size: 12px;
                text-align: center;
                white-space: nowrap;
            }
            .attendance-grid thead th {
                background: #8064A2;
                color: #fff;
                font-weight: 600;
            }
            .attendance-grid thead tr.sub-header th {
                background: #a68fc4; /* lighter purple instead of orange */
            }
            .attendance-grid td.emp-col {
                background: #fff;
                color: #1f2937;
                font-weight: 600;
                text-align: left;
            }
            .attendance-grid td.holiday {
                background: #DCE6F1;
                color: #2563eb;
                font-weight: 600;
            }
            .attendance-grid td.absent {
                background: #FDE9E9;
                color: #e11d48;
                font-weight: 600;
            }
            .attendance-grid td.leave {
                background: #FEF3C7;
                color: #b45309;
                font-weight: 600;
            }
            .attendance-legend {
                margin-top: 12px;
                font-size: 12px;
                color: #444;
                display: flex;
                gap: 24px;
                flex-wrap: wrap;
            }
        `;
        document.head.appendChild(style);
    }
 
    async fetch_and_render() {
        const args = {
            from_date: this.from_date.get_value(),
            to_date: this.to_date.get_value(),
            department: this.department.get_value() || null,
            employee: this.employee.get_value() || null,
        };
        if (!args.from_date || !args.to_date) return;
 
        this.$wrapper.html(`<div class="text-muted" style="padding:12px;">Loading…</div>`);
 
        const r = await frappe.call({ method: FULL_METHOD_PATH, args });
        const result = r.message;
        if (!result) return;
 
        this.last_result = result;
        this.last_filters = args;
        this.render_table(result);
    }
 
    render_table(result) {
        const { days, max_shifts, rows } = result;
        const shiftCols = max_shifts * 2;
 
        // ── Row 1: Employee ID / Name / Default Shift + merged date headers ──
        let headerRow1 = `
            <th rowspan="2">Employee ID</th>
            <th rowspan="2">Employee Name</th>
            <th rowspan="2">Default Shift</th>
        `;
        days.forEach((label) => {
            headerRow1 += `<th colspan="${shiftCols}">${frappe.utils.escape_html(label)}</th>`;
        });
 
        // ── Row 2: In/Out labels repeated per shift slot, per day ──
        let headerRow2 = "";
        days.forEach(() => {
            for (let i = 0; i < max_shifts; i++) {
                headerRow2 += `<th>In</th><th>Out</th>`;
            }
        });
 
        // ── Body rows ──
        let bodyRows = "";
        rows.forEach((row) => {
            bodyRows += `<tr>
                <td class="emp-col">${frappe.utils.escape_html(row.employee)}</td>
                <td class="emp-col">${frappe.utils.escape_html(row.employee_name)}</td>
                <td class="emp-col">${frappe.utils.escape_html(row.shift_names || "")}</td>`;
 
            row.days.forEach((day) => {
                if (day.type === "leave") {
                    bodyRows += `<td class="leave" colspan="${shiftCols}">On Leave${day.code ? ` (${frappe.utils.escape_html(day.code)})` : ""}</td>`;
                } else if (day.type === "holiday") {
                    bodyRows += `<td class="holiday" colspan="${shiftCols}">Holiday</td>`;
                } else if (day.type === "absent") {
                    bodyRows += `<td class="absent" colspan="${shiftCols}">Absent</td>`;
                } else {
                    day.slots.forEach((slot) => {
                        bodyRows += `<td>${slot.in || ""}</td><td>${slot.out || ""}</td>`;
                    });
                }
            });
 
            bodyRows += `</tr>`;
        });
 
        if (!rows.length) {
            bodyRows = `<tr><td colspan="3">No records found</td></tr>`;
        }
 
        this.$wrapper.html(`
            <table class="attendance-grid">
                <thead>
                    <tr>${headerRow1}</tr>
                    <tr class="sub-header">${headerRow2}</tr>
                </thead>
                <tbody>${bodyRows}</tbody>
            </table>
            <div class="attendance-legend">
                <span>Holiday</span>
                <span>Absent</span>
                <span>On Leave (L - Annual, C - Compassionate, P/M - Maternity/Paternity)</span>
            </div>
        `);
    }
 
    print_report() {
        if (!this.last_result) {
            frappe.msgprint("Nothing to print yet.");
            return;
        }
 
        const tableHtml = this.$wrapper.find("table")[0].outerHTML;
        const period = `${this.last_filters.from_date} to ${this.last_filters.to_date}`;
 
        const printWindow = window.open("", "_blank");
        printWindow.document.write(`
            <html>
            <head>
                <title>Monthly Attendance Report</title>
                <style>
                    body { font-family: Arial, sans-serif; padding: 24px; }
                    h2 { margin-bottom: 2px; }
                    .period { color: #555; margin-bottom: 16px; }
                    table.attendance-grid { border-collapse: collapse; width: 100%; }
                    table.attendance-grid th, table.attendance-grid td {
                        border: 1px solid #333;
                        padding: 4px 8px;
                        font-size: 11px;
                        text-align: left;
                        white-space: nowrap;
                    }
                    table.attendance-grid thead th {
                        text-align: center;
                        background: #8064A2 !important;
                        color: #fff !important;
                        -webkit-print-color-adjust: exact;
                        print-color-adjust: exact;
                    }
                    table.attendance-grid thead tr.sub-header th {
                        text-align: center;
                        background: #a68fc4 !important;
                        -webkit-print-color-adjust: exact;
                        print-color-adjust: exact;
                    }
                    td.emp-col { text-align: center; font-weight: 600; }
                    td.holiday { background: #DCE6F1 !important; color: #2563eb; -webkit-print-color-adjust: exact; }
                    td.absent { background: #FDE9E9 !important; color: #e11d48; -webkit-print-color-adjust: exact; }
                    td.leave { background: #FEF3C7 !important; color: #b45309; -webkit-print-color-adjust: exact; }
                    .attendance-legend { margin-top: 12px; font-size: 12px; display: flex; gap: 24px; flex-wrap: wrap; }
 
                    /* ── Signature footer ── */
                    .signatures {
                        display: flex;
                        justify-content: space-between;
                        margin-top: 60px;
                        page-break-inside: avoid;
                    }
                    .signature-block {
                        width: 40%;
                        text-align: center;
                    }
                    .signature-line {
                        border-top: 1px solid #333;
                        margin-top: 40px;
                        padding-top: 6px;
                        font-size: 12px;
                        font-weight: 600;
                    }
                </style>
            </head>
            <body>
                <h2>Monthly Attendance Report</h2>
                <div class="period">Period: ${period}</div>
                ${tableHtml}
 
                <div class="signatures">
                    <div class="signature-block">
                        <div class="signature-line">HR Manager</div>
                    </div>
                    <div class="signature-block">
                        <div class="signature-line">Line Manager</div>
                    </div>
                </div>
            </body>
            </html>
        `);
        printWindow.document.close();
        printWindow.onload = () => {
            printWindow.focus();
            printWindow.print();
        };
    }
 
    export_excel() {
        if (!this.last_filters) {
            frappe.msgprint("Nothing to export yet.");
            return;
        }
        const params = new URLSearchParams();
        Object.entries(this.last_filters).forEach(([k, v]) => {
            if (v) params.append(k, v);
        });
        const url = `/api/method/${EXCEL_METHOD_PATH}?${params.toString()}`;
        window.open(url, "_blank");
    }
}