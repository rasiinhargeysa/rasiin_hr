// Copyright (c) 2023, Rasiin and contributors
// For license information, please see license.txt

// NOTE: update these two dotted paths to match your actual app/module
// if it differs from what's shown here.
const CALENDAR_METHOD_PATH =
	"rasiin_hr.rasiin_hr.report.personal_attendace_report.personal_attendace_report.get_personal_attendance_calendar";
const CALENDAR_EXCEL_METHOD_PATH =
	"rasiin_hr.rasiin_hr.report.personal_attendace_report.personal_attendace_report.download_monthly_calendar";

frappe.pages["personal-attendance-"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Personal Attendance Report",
		single_column: true,
	});

	const calendar = new PersonalAttendanceCalendar(page);

	page.set_primary_action("Print", () => calendar.print_report(), "printer");
	page.add_inner_button("Export to Excel", () => calendar.export_excel());
};

const CODE_LABELS = {
	A: "A",
	L: "L",
	C: "C",
	"P/M": "P/M",
	UA: "UA",
};

class PersonalAttendanceCalendar {
	constructor(page) {
		this.page = page;
		this.build_filters();
		this.build_container();
	}

	build_filters() {
		this.employee = this.page.add_field({
			fieldname: "employee",
			label: "Employee",
			fieldtype: "Link",
			options: "Employee",
			reqd: 1,
			change: () => this.fetch_and_render(),
		});
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
	}

	build_container() {
		this.$wrapper = $(`<div class="personal-attendance-wrapper"></div>`);
		this.page.main.append(this.$wrapper);
		this.inject_styles();
		this.$wrapper.html(
			`<div class="text-muted" style="padding:12px;">Select an Employee to view their monthly attendance.</div>`
		);
	}

	inject_styles() {
		if (document.getElementById("personal-attendance-style")) return;
		const style = document.createElement("style");
		style.id = "personal-attendance-style";
		style.innerHTML = `
			.pa-header-block {
				display: flex;
				flex-wrap: wrap;
				gap: 0;
				margin-bottom: 12px;
				border: 1px solid #d1d5db;
			}
			.pa-header-cell {
				display: flex;
				align-items: center;
				border-right: 1px solid #d1d5db;
			}
			.pa-header-cell:last-child { border-right: none; }
			.pa-header-label {
				background: #7A5EA7;
				color: #fff;
				font-weight: 600;
				padding: 6px 12px;
				white-space: nowrap;
			}
			.pa-header-value {
				padding: 6px 12px;
				white-space: nowrap;
			}
			.attendance-grid { border-collapse: collapse; width: max-content; min-width: 100%; }
			.attendance-grid th, .attendance-grid td {
				border: 1px solid #d1d5db;
				padding: 4px 8px;
				font-size: 12px;
				text-align: center;
				white-space: nowrap;
			}
			.attendance-grid thead th {
				background: #7A5EA7;
				color: #fff;
				font-weight: 600;
			}
			.attendance-grid td.pa-date {
				background: #fff;
				font-weight: 600;
				text-align: left;
			}
			.attendance-grid td.pa-absent { background: #FDE9E9; color: #e11d48; font-weight: 600; }
			.attendance-grid td.pa-leave { background: #DCE6F1; color: #2563eb; font-weight: 600; }
			.pa-legend { margin-top: 12px; font-size: 12px; color: #444; display: flex; gap: 24px; flex-wrap: wrap; }
			.pa-signatures {
				display: flex;
				justify-content: space-between;
				margin-top: 60px;
			}
			.pa-signature-block { width: 30%; text-align: center; }
			.pa-signature-line {
				border-top: 1px solid #333;
				margin-top: 40px;
				padding-top: 6px;
				font-size: 12px;
				font-weight: 600;
			}
		`;
		document.head.appendChild(style);
	}

	async fetch_and_render() {
		const employee = this.employee.get_value();
		const from_date = this.from_date.get_value();
		const to_date = this.to_date.get_value();

		if (!employee || !from_date || !to_date) return;

		this.$wrapper.html(`<div class="text-muted" style="padding:12px;">Loading…</div>`);

		const r = await frappe.call({
			method: CALENDAR_METHOD_PATH,
			args: { employee, from_date, to_date },
		});
		const result = r.message;
		if (!result) return;

		this.last_result = result;
		this.last_filters = { employee, from_date, to_date };
		this.render_calendar(result);
	}

	header_block_html(result) {
		return `
			<div class="pa-header-block">
				<div class="pa-header-cell">
					<div class="pa-header-label">Employee ID</div>
					<div class="pa-header-value">${frappe.utils.escape_html(result.employee)}</div>
				</div>
				<div class="pa-header-cell">
					<div class="pa-header-label">Employee Name</div>
					<div class="pa-header-value">${frappe.utils.escape_html(result.employee_name)}</div>
				</div>
				<div class="pa-header-cell">
					<div class="pa-header-label">Job title</div>
					<div class="pa-header-value">${frappe.utils.escape_html(result.designation || "")}</div>
				</div>
				<div class="pa-header-cell">
					<div class="pa-header-label">Department</div>
					<div class="pa-header-value">${frappe.utils.escape_html(result.department || "")}</div>
				</div>
			</div>
		`;
	}

	table_html(result) {
		let head = `
			<tr>
				<th>Date</th>
				<th>In</th><th>Out</th><th>In</th><th>Out</th>
				<th>Mandatory<br>Hours</th>
				<th>Actual hours<br>worked</th>
				<th>Late -in<br>mins</th>
				<th>Less Hours<br>worked</th>
				<th>Overtime</th>
			</tr>
		`;

		let body = "";
		result.rows.forEach((row) => {
			body += `<tr><td class="pa-date">${frappe.utils.escape_html(row.date_label)}</td>`;

			if (row.code) {
				const cls = row.code === "A" || row.code === "UA" ? "pa-absent" : "pa-leave";
				body += `<td class="${cls}" colspan="4">${frappe.utils.escape_html(CODE_LABELS[row.code] || row.code)}</td>`;
			} else {
				body += `
					<td>${row.am_in || ""}</td>
					<td>${row.am_out || ""}</td>
					<td>${row.pm_in || ""}</td>
					<td>${row.pm_out || ""}</td>
				`;
			}

			body += `
				<td>${row.mandatory_hours || ""}</td>
				<td>${row.actual_hours || ""}</td>
				<td>${row.late_in_minutes || ""}</td>
				<td>${row.less_hours || ""}</td>
				<td>${row.overtime || ""}</td>
			</tr>`;
		});

		if (!result.rows.length) {
			body = `<tr><td colspan="10">No records found</td></tr>`;
		}

		return `<table class="attendance-grid"><thead>${head}</thead><tbody>${body}</tbody></table>`;
	}

	legend_html() {
		return `
			<div class="pa-legend">
				<span>A - Absence</span>
				<span>L - Annual leave</span>
				<span>C - Compassionate leave</span>
				<span>P/M - Maternity</span>
				<span>UA - Unjustified Absence</span>
			</div>
		`;
	}

	render_calendar(result) {
		this.$wrapper.html(`
			${this.header_block_html(result)}
			<div style="overflow-x:auto;">${this.table_html(result)}</div>
			${this.legend_html()}
		`);
	}

	print_report() {
		if (!this.last_result) {
			frappe.msgprint("Select an Employee and load the report first.");
			return;
		}

		const period = `${this.last_filters.from_date} to ${this.last_filters.to_date}`;
		const bodyHtml = `
			${this.header_block_html(this.last_result)}
			${this.table_html(this.last_result)}
			${this.legend_html()}
			<div class="pa-signatures">
				<div class="pa-signature-block"><div class="pa-signature-line">Employee signature</div></div>
				<div class="pa-signature-block"><div class="pa-signature-line">Manager/Supervisor signature</div></div>
				<div class="pa-signature-block"><div class="pa-signature-line">HR Manager Signature</div></div>
			</div>
		`;

		const printWindow = window.open("", "_blank");
		printWindow.document.write(`
			<html>
			<head>
				<title>Personal Attendance Report</title>
				<style>
					body { font-family: Arial, sans-serif; padding: 24px; }
					h2 { margin-bottom: 2px; }
					.period { color: #555; margin-bottom: 16px; }
					.pa-header-block { display: flex; flex-wrap: wrap; margin-bottom: 12px; border: 1px solid #333; }
					.pa-header-cell { display: flex; align-items: center; border-right: 1px solid #333; }
					.pa-header-cell:last-child { border-right: none; }
					.pa-header-label {
						background: #7A5EA7 !important; color: #fff !important; font-weight: 600; padding: 6px 12px;
						-webkit-print-color-adjust: exact; print-color-adjust: exact;
					}
					.pa-header-value { padding: 6px 12px; }
					table.attendance-grid { border-collapse: collapse; width: 100%; }
					table.attendance-grid th, table.attendance-grid td {
						border: 1px solid #333; padding: 4px 8px; font-size: 11px; text-align: center;
					}
					table.attendance-grid thead th {
						background: #7A5EA7 !important; color: #fff !important;
						-webkit-print-color-adjust: exact; print-color-adjust: exact;
					}
					td.pa-absent { background: #FDE9E9 !important; color: #e11d48; -webkit-print-color-adjust: exact; }
					td.pa-leave { background: #DCE6F1 !important; color: #2563eb; -webkit-print-color-adjust: exact; }
					.pa-legend { margin-top: 12px; font-size: 12px; display: flex; gap: 24px; flex-wrap: wrap; }
					.pa-signatures { display: flex; justify-content: space-between; margin-top: 60px; page-break-inside: avoid; }
					.pa-signature-block { width: 30%; text-align: center; }
					.pa-signature-line { border-top: 1px solid #333; margin-top: 40px; padding-top: 6px; font-size: 12px; font-weight: 600; }
				</style>
			</head>
			<body>
				<h2>Personal Attendance Report</h2>
				<div class="period">Period: ${period}</div>
				${bodyHtml}
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
			frappe.msgprint("Select an Employee and load the report first.");
			return;
		}
		const params = new URLSearchParams(this.last_filters);
		const url = frappe.urllib.get_full_url(`/api/method/${CALENDAR_EXCEL_METHOD_PATH}?${params.toString()}`);
		window.open(url, "_blank");
	}
}