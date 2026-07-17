frappe.query_reports["Employee List Report"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            on_change: function(report) { report.refresh(); }
        },
        {
            fieldname: "department",
            label: __("Department"),
            fieldtype: "Link",
            options: "Department",
            on_change: function(report) { report.refresh(); }
        },
        {
            fieldname: "designation",
            label: __("Designation"),
            fieldtype: "Link",
            options: "Designation",
            on_change: function(report) { report.refresh(); }
        },
        {
            fieldname: "employment_type",
            label: __("Employment Type"),
            fieldtype: "Link",
            options: "Employment Type",
            on_change: function(report) { report.refresh(); }
        },
        {
            fieldname: "status",
            label: __("Status"),
            fieldtype: "Select",
            options: "\nActive\nInactive\nSuspended\nLeft",
            on_change: function(report) { report.refresh(); }
        },
    ],
};