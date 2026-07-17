// Copyright (c) 2023, Rasiin and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Employee Attendance Report"] = {
	"filters": [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.now_date(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.now_date(),
			reqd: 1,
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "worked_on_holiday",
			label: __("Worked On Holiday"),
			fieldtype: "Check",
			default: 0,
			hidden: 1
		},
	],

	"order_by": "attendance_date asc",

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname == "status") {
			if (value == "Present") {
				value = "<span style='color:green;font-weight:600'>" + value + "</span>";
			} else if (value == "Absent") {
				value = "<span style='color:red;font-weight:600'>" + value + "</span>";
			} else if (value == "Half Day") {
				value = "<span style='color:orange;font-weight:600'>" + value + "</span>";
			} else if (value == "On Leave") {
				value = "<span style='color:#318AD8;font-weight:600'>" + value + "</span>";
			} else if (value == "Holiday") {
				value = "<span style='color:#999;font-weight:600'>" + value + "</span>";
			}
		}

		return value;
	},
};