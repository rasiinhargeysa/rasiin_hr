# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.utils import flt

import erpnext

def execute(filters=None):
	if not filters:
		filters = {}
	currency = None
	if filters.get("currency"):
		currency = filters.get("currency")
	company_currency = erpnext.get_company_currency(filters.get("company"))
	salary_slips = get_salary_slips(filters, company_currency)
	if not salary_slips:
		return [], []

	columns, earning_types, ded_types = get_columns(salary_slips)
	ss_earning_map = get_ss_earning_map(salary_slips, currency, company_currency)
	ss_ded_map = get_ss_ded_map(salary_slips, currency, company_currency)

	# Department column width fix done once, outside the per-row loop
	if columns[3] is not None:
		columns[3] = columns[3].replace("-1", "120")

	data = []
	for ss in salary_slips:
		row = [
			ss.name,
			ss.employee,
			ss.employee_name,
			ss.department,
			ss.bank_name,
			ss.bank_ac_no,
		]

		for e in earning_types:
			row.append(ss_earning_map.get(ss.name, {}).get(e))

		if currency == company_currency:
			row += [flt(ss.gross_pay) * flt(ss.exchange_rate)]
		else:
			row += [ss.gross_pay]

		for d in ded_types:
			row.append(ss_ded_map.get(ss.name, {}).get(d))

		if currency == company_currency:
			row += [
				flt(ss.total_deduction) * flt(ss.exchange_rate),
				flt(ss.net_pay) * flt(ss.exchange_rate),
			]
		else:
			row += [ss.total_deduction, ss.net_pay]

		row.append(ss.paid_amount)
		row.append(flt(ss.net_pay) - flt(ss.paid_amount))
		row.append(currency or company_currency)

		data.append(row)

	return columns, data


def get_columns(salary_slips):
	columns = [
		_("Salary Slip ID") + ":Link/Salary Slip:150",
		_("Employee") + ":Link/Employee:120",
		_("Employee Name") + "::200",
		_("Department") + ":Link/Department:-1",
		_("Bank Name") + "::120",
		_("Bank Account No") + "::120",
	]

	salary_components = {_("Earning"): [], _("Deduction"): []}

	for component in frappe.db.sql(
		"""select distinct sd.salary_component, sc.type
		from `tabSalary Detail` sd, `tabSalary Component` sc
		where sc.name=sd.salary_component and sd.amount != 0 and sd.parent in (%s)"""
		% (", ".join(["%s"] * len(salary_slips))),
		tuple([d.name for d in salary_slips]),
		as_dict=1,
	):
		salary_components[_(component.type)].append(component.salary_component)

	columns = (
		columns
		+ [(e + ":Currency:120") for e in salary_components[_("Earning")]]
		+ [_("Gross Pay") + ":Currency:120"]
		+ [(d + ":Currency:120") for d in salary_components[_("Deduction")]]
		+ [
			_("Total Deduction") + ":Currency:120",
			_("Net Pay") + ":Currency:120",
			_("Paid Amount") + ":Currency:120",
			_("Balance") + ":Currency:120",
			# _("Currency") + ":Link/Currency:80",
		]
	)

	return columns, salary_components[_("Earning")], salary_components[_("Deduction")]



def get_salary_slips(filters, company_currency):
	filters.update({"from_date": filters.get("from_date"), "to_date": filters.get("to_date")})
	conditions, filters = get_conditions(filters, company_currency)
	salary_slips = frappe.db.sql(
		"""select ss.*, emp.bank_name as bank_name, emp.bank_ac_no as bank_ac_no
		from `tabSalary Slip` ss
		left join `tabEmployee` emp on emp.name = ss.employee
		where %s
		order by ss.employee"""
		% conditions,
		filters,
		as_dict=1,
	)

	return salary_slips or []


def get_conditions(filters, company_currency):
	conditions = ""
	doc_status = {"Draft": 0, "Submitted": 1, "Cancelled": 2}

	if filters.get("docstatus"):
		conditions += "ss.docstatus = {0}".format(doc_status[filters.get("docstatus")])
	else:
		conditions += "1=1"

	if filters.get("from_date"):
		conditions += " and ss.start_date >= %(from_date)s"
	if filters.get("to_date"):
		conditions += " and ss.end_date <= %(to_date)s"
	if filters.get("company"):
		conditions += " and ss.company = %(company)s"
	if filters.get("employee"):
		conditions += " and ss.employee = %(employee)s"
	if filters.get("currency") and filters.get("currency") != company_currency:
		conditions += " and ss.currency = %(currency)s"
	if filters.get("branch"):
		conditions += " and emp.branch = %(branch)s"

	return conditions, filters


def get_ss_earning_map(salary_slips, currency, company_currency):
	ss_earnings = frappe.db.sql(
		"""select sd.parent, sd.salary_component, sd.amount, ss.exchange_rate, ss.name
		from `tabSalary Detail` sd, `tabSalary Slip` ss where sd.parent=ss.name and sd.parent in (%s)"""
		% (", ".join(["%s"] * len(salary_slips))),
		tuple([d.name for d in salary_slips]),
		as_dict=1,
	)

	ss_earning_map = {}
	for d in ss_earnings:
		ss_earning_map.setdefault(d.parent, frappe._dict()).setdefault(d.salary_component, 0.0)
		if currency == company_currency:
			ss_earning_map[d.parent][d.salary_component] += flt(d.amount) * flt(
				d.exchange_rate if d.exchange_rate else 1
			)
		else:
			ss_earning_map[d.parent][d.salary_component] += flt(d.amount)

	return ss_earning_map


def get_ss_ded_map(salary_slips, currency, company_currency):
	ss_deductions = frappe.db.sql(
		"""select sd.parent, sd.salary_component, sd.amount, ss.exchange_rate, ss.name
		from `tabSalary Detail` sd, `tabSalary Slip` ss where sd.parent=ss.name and sd.parent in (%s)"""
		% (", ".join(["%s"] * len(salary_slips))),
		tuple([d.name for d in salary_slips]),
		as_dict=1,
	)

	ss_ded_map = {}
	for d in ss_deductions:
		ss_ded_map.setdefault(d.parent, frappe._dict()).setdefault(d.salary_component, 0.0)
		if currency == company_currency:
			ss_ded_map[d.parent][d.salary_component] += flt(d.amount) * flt(
				d.exchange_rate if d.exchange_rate else 1
			)
		else:
			ss_ded_map[d.parent][d.salary_component] += flt(d.amount)

	return ss_ded_map