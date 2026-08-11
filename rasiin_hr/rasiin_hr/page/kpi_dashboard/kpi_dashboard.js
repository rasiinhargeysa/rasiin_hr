let current_data = null;
let from_date_field, to_date_field, department_field, status_field, priority_field;

frappe.pages['kpi-dashboard'].on_page_load = function (wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'KPI Performance Report',
        single_column: true
    });

    page.add_inner_button('Export to Excel', () => export_to_excel());
    page.add_inner_button('Export to PDF', () => export_to_pdf());

    $(wrapper).find('.layout-main-section').append(`
        <style>
            .kpi-wrap { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }
            .kpi-filter-row {
                display:flex; gap:10px; align-items:flex-end; margin-bottom:24px; flex-wrap:wrap;
                padding:16px; background:#fafbfc; border:1px solid #e5e7eb; border-radius:8px;
            }
            .kpi-filter-row .frappe-control { width:170px; }

            .kpi-tile-row { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:16px; margin-bottom:24px; }
            .kpi-tile { border-radius:12px; padding:18px; }
            .kpi-tile .label { font-size:12px; margin-bottom:6px; }
            .kpi-tile .value { font-size:28px; font-weight:600; }
            .kpi-tile .sub { font-size:11px; margin-top:4px; opacity:.85; }
            .t1 { background:#e6f1fb; } .t1 .label,.t1 .value,.t1 .sub { color:#0c447c; }
            .t2 { background:#eaf3de; } .t2 .label,.t2 .value,.t2 .sub { color:#27500a; }
            .t3 { background:#faeeda; } .t3 .label,.t3 .value,.t3 .sub { color:#633806; }
            .t4 { background:#e8e6fb; } .t4 .label,.t4 .value,.t4 .sub { color:#3b2f8c; }
            .t5 { background:#fbe6e6; } .t5 .label,.t5 .value,.t5 .sub { color:#7c0c0c; }
            .t6 { background:#dcf4f1; } .t6 .label,.t6 .value,.t6 .sub { color:#0f5c52; }
            .t7 { background:#eef2f7; } .t7 .label,.t7 .value,.t7 .sub { color:#334155; }

            .bar-box { background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:16px; margin-bottom:24px; }
            .bar-box h4 { margin:0 0 12px; font-size:14px; font-weight:600; color:#374151; }
            .bar-box .hint { font-size:11px; color:#6b7280; margin:-6px 0 12px; }
            .bar-row { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
            .bar-row .name { width:130px; font-size:12px; color:#374151; }
            .bar-track { flex:1; background:#f1f5f9; border-radius:4px; height:14px; }
            .bar-fill { height:14px; border-radius:4px; }
            .bar-row .bar-value { width:44px; font-size:12px; text-align:right; color:#374151; }

            .chip-row { display:flex; flex-wrap:wrap; gap:8px; }
            .chip {
                display:inline-flex; align-items:center; gap:8px;
                border:1px solid #e5e7eb; border-radius:20px; padding:6px 14px;
                font-size:12px; background:#fff; color:#374151;
            }
            .chip b { font-size:14px; }

            .pill {
                display:inline-block; padding:3px 10px; border-radius:10px;
                font-size:11px; font-weight:600; white-space:nowrap;
            }
            .st-not-started { background:#e5e7eb; color:#374151; }
            .st-pending     { background:#dbeafe; color:#1e40af; }
            .st-in-progress { background:#fef9c3; color:#854d0e; }
            .st-completed   { background:#dcfce7; color:#166534; }
            .st-overdue     { background:#fee2e2; color:#991b1b; }
            .pr-high   { background:#fee2e2; color:#991b1b; }
            .pr-middle { background:#fef9c3; color:#854d0e; }
            .pr-low    { background:#e0f2fe; color:#075985; }
            .dl-ontime   { background:#dcfce7; color:#166534; }
            .dl-late     { background:#ffedd5; color:#9a3412; }
            .dl-overdue  { background:#fee2e2; color:#991b1b; }
            .dl-upcoming { background:#dbeafe; color:#1e40af; }
            .dl-none     { background:#e5e7eb; color:#374151; }
            .date-late { color:#991b1b; font-weight:600; }

            .kpi-detail-table { width:100%; border-collapse:collapse; font-size:13px; }
            .kpi-detail-table th {
                background:#f5b942; color:#1f2937; font-weight:600; text-align:center;
                padding:10px 8px; border:1px solid #e0a627;
            }
            .kpi-detail-table td {
                padding:8px; border:1px solid #e5e7eb; text-align:center; color:#1f2937;
            }
            .kpi-detail-table td.kra-cell, .kpi-detail-table td.kpi-cell { text-align:left; }
            .kpi-detail-table td.final-col { font-weight:600; }
            .kpi-detail-table tr.total-row td {
                background:#f5a623; color:#1f2937; font-weight:700; text-align:right; padding:10px 8px;
            }

            .kpi-detail-table tr.row-red td { background:#fee2e2 !important; }
            .kpi-detail-table tr.row-yellow td { background:#fef9c3 !important; }
            .kpi-detail-table tr.row-green td { background:#dcfce7 !important; }

            .score-badge {
                display:inline-block; min-width:36px; padding:3px 10px; border-radius:10px;
                font-weight:600; font-size:12px;
            }
            .score-red { background:#fee2e2; color:#991b1b; }
            .score-yellow { background:#fef9c3; color:#854d0e; }
            .score-green { background:#dcfce7; color:#166534; }

            .kpi-empty-state {
                padding:40px 16px; text-align:center; color:#6b7280;
                background:#fafbfc; border:1px dashed #e5e7eb; border-radius:8px; margin-bottom:24px;
            }
        </style>

        <div class="kpi-wrap">
            <div class="kpi-filter-row">
                <div class="frappe-control" id="from-date-field"></div>
                <div class="frappe-control" id="to-date-field"></div>
                <div class="frappe-control" id="department-field"></div>
                <div class="frappe-control" id="status-field"></div>
                <div class="frappe-control" id="priority-field"></div>
                <button class="btn btn-default btn-sm" id="clear-filter">Clear</button>
            </div>

            <div id="kpi-empty-state" class="kpi-empty-state" style="display:none;">
                Please select both <strong>From Date</strong> and <strong>To Date</strong> to view the report.
            </div>

            <div class="kpi-tile-row" id="kpi-tiles"></div>

            <div class="bar-box">
                <h4>Average score by key result area</h4>
                <div id="kra-bars"></div>
            </div>

            <div class="bar-box">
                <h4>Deadline summary</h4>
                <p class="hint">On time means completed while the ending date was still in the future.</p>
                <div id="deadline-chips" class="chip-row"></div>
            </div>

            <div class="bar-box">
                <h4>Status breakdown</h4>
                <div id="status-chips" class="chip-row"></div>
            </div>

            <div class="bar-box">
                <h4>Priority breakdown</h4>
                <div id="priority-chips" class="chip-row"></div>
            </div>

            <div class="bar-box">
                <h4>KPI detail</h4>
                <div id="kpi-detail-table" style="overflow-x:auto;"></div>
            </div>
        </div>
    `);

    from_date_field = frappe.ui.form.make_control({
        parent: $('#from-date-field'),
        df: {
            fieldtype: 'Date',
            fieldname: 'from_date',
            label: 'From Date',
            reqd: 1,
            onchange: () => try_load_dashboard()
        },
        render_input: true
    });
    to_date_field = frappe.ui.form.make_control({
        parent: $('#to-date-field'),
        df: {
            fieldtype: 'Date',
            fieldname: 'to_date',
            label: 'To Date',
            reqd: 1,
            onchange: () => try_load_dashboard()
        },
        render_input: true
    });
    department_field = frappe.ui.form.make_control({
        parent: $('#department-field'),
        df: {
            fieldtype: 'Link',
            options: 'Department',
            fieldname: 'department',
            label: 'Department',
            onchange: () => try_load_dashboard()
        },
        render_input: true
    });
    status_field = frappe.ui.form.make_control({
        parent: $('#status-field'),
        df: {
            fieldtype: 'Select',
            fieldname: 'status',
            label: 'Status',
            options: ['', 'Not Started', 'Pending', 'In progress', 'completed', 'Overdue'],
            onchange: () => try_load_dashboard()
        },
        render_input: true
    });
    priority_field = frappe.ui.form.make_control({
        parent: $('#priority-field'),
        df: {
            fieldtype: 'Select',
            fieldname: 'priority',
            label: 'Priority',
            options: ['', 'High', 'Middle', 'Low'],
            onchange: () => try_load_dashboard()
        },
        render_input: true
    });

    from_date_field.refresh();
    to_date_field.refresh();
    department_field.refresh();
    status_field.refresh();
    priority_field.refresh();

    $('#clear-filter').on('click', () => {
        from_date_field.set_value('');
        to_date_field.set_value('');
        department_field.set_value('');
        status_field.set_value('');
        priority_field.set_value('');
        show_empty_state();
    });

    // Start empty — dates are required before any data is fetched
    show_empty_state();

    function try_load_dashboard() {
        let from_date = from_date_field.get_value();
        let to_date = to_date_field.get_value();
        let department = department_field.get_value();
        let status = status_field.get_value();
        let priority = priority_field.get_value();

        if (!from_date || !to_date) {
            show_empty_state();
            return;
        }
        if (from_date > to_date) {
            frappe.msgprint(__('From Date cannot be after To Date'));
            return;
        }
        load_dashboard(from_date, to_date, department, status, priority);
    }

    function show_empty_state() {
        current_data = null;
        $('#kpi-empty-state').show();
        $('#kpi-tiles').empty();
        $('#kra-bars').empty();
        $('#deadline-chips').empty();
        $('#status-chips').empty();
        $('#priority-chips').empty();
        $('#kpi-detail-table').empty();
    }

    function load_dashboard(from_date, to_date, department, status, priority) {
        frappe.call({
            method: "rasiin_hr.rasiin_hr.page.kpi_dashboard.kpi_dashboard.get_kpi_dashboard_data",
            args: { from_date, to_date, department, status, priority },
            callback: function (r) {
                if (!r.message) return;
                $('#kpi-empty-state').hide();
                current_data = r.message;
                render_tiles(r.message.cards);
                render_kra_bars(r.message.kra_avg);
                render_deadline_chips(r.message.deadline_summary);
                render_chips('#status-chips', r.message.status_summary, get_status_class);
                render_chips('#priority-chips', r.message.priority_summary, get_priority_class);
                render_detail_table(r.message.detail_rows, r.message.total_final, r.message.total_final_label);
            }
        });
    }

    function render_tiles(c) {
        $('#kpi-tiles').html(`
            <div class="kpi-tile t1"><div class="label">Total KPI entries</div><div class="value">${c.total_kpi_items}</div></div>
            <div class="kpi-tile t2"><div class="label">Overall avg final score</div><div class="value">${c.overall_avg}</div></div>
            <div class="kpi-tile t3"><div class="label">Weak areas (&lt;60)</div><div class="value">${c.weak_areas}</div></div>
            <div class="kpi-tile t4"><div class="label">Finished</div><div class="value">${c.finished_count}</div><div class="sub">${c.completion_pct}% of all entries</div></div>
            <div class="kpi-tile t6"><div class="label">Finished by deadline</div><div class="value">${c.finished_on_time}</div><div class="sub">${c.on_time_pct}% on-time rate</div></div>
            <div class="kpi-tile t5"><div class="label">Overdue</div><div class="value">${c.overdue_count}</div><div class="sub">${c.finished_late} finished late</div></div>
            <div class="kpi-tile t7"><div class="label">Due upcoming</div><div class="value">${c.upcoming_count}</div><div class="sub">deadline still ahead</div></div>
        `);
    }

    // ---- Score bands (0–100): <=60 red, 61–85 yellow, >=86 green ----
    // Used by both the detail table row highlight and the KRA bars so the
    // two can never drift apart.
    const MAX_KRA_SCORE = 100;
    const SCORE_BANDS = [
        { max: 60,       color: '#ef4444', cls: 'row-red' },
        { max: 85,       color: '#f5b942', cls: 'row-yellow' },
        { max: Infinity, color: '#22c55e', cls: 'row-green' }
    ];

    function get_score_band(score) {
        score = Number(score) || 0;
        return SCORE_BANDS.find(b => score <= b.max);
    }

    function get_row_class(score) {
        if (score === null || score === undefined || score === '') return '';
        return get_score_band(score).cls;
    }

    // ---- Final score badge (0–5 scale): <3 red, 3-4 yellow, >4 green ----
    function get_score_class(score) {
        score = Number(score) || 0;
        if (score < 3) return 'score-red';
        if (score <= 4) return 'score-yellow';
        return 'score-green';
    }

    // ---- Status / Priority pill colours ----
    const STATUS_CLASSES = {
        'Not Started': 'st-not-started',
        'Pending': 'st-pending',
        'In progress': 'st-in-progress',
        'completed': 'st-completed',
        'Overdue': 'st-overdue'
    };
    const PRIORITY_CLASSES = {
        'High': 'pr-high',
        'Middle': 'pr-middle',
        'Low': 'pr-low'
    };

    function get_status_class(status) {
        return STATUS_CLASSES[status] || 'st-not-started';
    }

    function get_priority_class(priority) {
        return PRIORITY_CLASSES[priority] || 'pr-low';
    }

    function render_pill(value, cls) {
        if (!value) return '';
        return `<span class="pill ${cls}">${frappe.utils.escape_html(value)}</span>`;
    }

    function render_kra_bars(kra) {
        if (!kra.length) { $('#kra-bars').html('<p class="text-muted">No data available</p>'); return; }
        let sorted = [...kra].sort((a, b) => (Number(b.avg) || 0) - (Number(a.avg) || 0));
        $('#kra-bars').html(sorted.map(k => `
            <div class="bar-row">
                <span class="name">${frappe.utils.escape_html(k.kra || '')}</span>
                <div class="bar-track">
                    <div class="bar-fill" style="width:${Math.min((k.avg / MAX_KRA_SCORE) * 100, 100)}%; background:${get_score_band(k.avg).color};"></div>
                </div>
                <span class="bar-value">${k.avg}</span>
            </div>
        `).join(''));
    }

    function render_chips(selector, list, cls_fn) {
        if (!list || !list.length) { $(selector).html('<p class="text-muted">No data available</p>'); return; }
        $(selector).html(list.map(x => `
            <span class="chip">${render_pill(x.label, cls_fn(x.label))}<b>${x.count}</b></span>
        `).join(''));
    }

    function render_deadline_chips(list) {
        if (!list || !list.length) { $('#deadline-chips').html('<p class="text-muted">No data available</p>'); return; }
        $('#deadline-chips').html(list.map(x => `
            <span class="chip">${render_pill(x.label, x.cls)}<b>${x.count}</b></span>
        `).join(''));
    }

    function render_detail_table(rows, total_final, total_final_label) {
        if (!rows.length) { $('#kpi-detail-table').html('<p class="text-muted">No data available</p>'); return; }
        let body = rows.map(r => `
            <tr class="${get_row_class(r.score)}">
                <td>${r.no}</td>
                <td class="kra-cell">${r.kra || ''}</td>
                <td class="kpi-cell">${r.kpi || ''}</td>
                <td>${r.weight}</td>
                <td>${r.target != null ? r.target : ''}</td>
                <td>${r.actual != null ? r.actual : ''}</td>
                <td class="${r.is_late ? 'date-late' : ''}">${r.ending_date ? frappe.datetime.str_to_user(r.ending_date) : ''}</td>
                <td>${render_pill(r.status, get_status_class(r.status))}</td>
                <td>${render_pill(r.priority, get_priority_class(r.priority))}</td>
                <td>${r.score}</td>
                <td class="final-col"><span class="score-badge ${get_score_class(r.final)}">${r.final}</span></td>
            </tr>
        `).join('');
        $('#kpi-detail-table').html(`
            <table class="kpi-detail-table">
                <thead>
                    <tr>
                        <th>No.</th><th>Key result areas</th><th>Key performance indicators</th>
                        <th>Weight of KPIs</th><th>Target</th><th>Actual</th>
                        <th>Ending date</th><th>Status</th><th>Priority</th>
                        <th>Score</th><th>Final score</th>
                    </tr>
                </thead>
                <tbody>
                    ${body}
                    <tr class="total-row"><td colspan="10">${total_final_label || 'Total'}</td><td>${total_final}</td></tr>
                </tbody>
            </table>
        `);
    }

    function export_to_excel() {
        let from_date = from_date_field.get_value();
        let to_date = to_date_field.get_value();
        let department = department_field.get_value();
        let status = status_field.get_value();
        let priority = priority_field.get_value();
        if (!from_date || !to_date) {
            frappe.msgprint(__('Please select From Date and To Date before exporting'));
            return;
        }
        window.open(
            `/api/method/rasiin_hr.rasiin_hr.page.kpi_dashboard.kpi_dashboard.export_kpi_excel?from_date=${from_date || ''}&to_date=${to_date || ''}&department=${department || ''}&status=${status || ''}&priority=${priority || ''}`
        );
    }

    function export_to_pdf() {
        if (!current_data) {
            frappe.msgprint(__('Please select From Date and To Date before exporting'));
            return;
        }
        let content = $('.kpi-wrap').html();
        let win = window.open('', '_blank');
        win.document.write(`
            <html>
            <head>
                <title>KPI Report</title>
                <style>
                    body { font-family: -apple-system, Roboto, sans-serif; padding: 24px; }
                    .kpi-filter-row, .btn, .kpi-empty-state { display: none !important; }
                    .kpi-detail-table { width:100%; border-collapse:collapse; font-size:12px; }
                    .kpi-detail-table th { background:#f5b942; color:#1f2937; padding:8px; border:1px solid #e0a627; }
                    .kpi-detail-table td { padding:6px 8px; border:1px solid #e5e7eb; text-align:center; }
                    .kpi-detail-table td.kra-cell, .kpi-detail-table td.kpi-cell { text-align:left; }
                    .kpi-detail-table td.final-col { background:#e3edcf; font-weight:600; }
                    .kpi-detail-table tr.total-row td { background:#f5a623; font-weight:700; text-align:right; }
                    .kpi-detail-table tr.row-red td { background:#fee2e2 !important; }
                    .kpi-detail-table tr.row-yellow td { background:#fef9c3 !important; }
                    .kpi-detail-table tr.row-green td { background:#dcfce7 !important; }
                    .kpi-tile-row { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:20px; }
                    .kpi-tile { flex:1 1 150px; border-radius:8px; padding:12px; }
                    .kpi-tile .value { font-size:22px; font-weight:600; }
                    .kpi-tile .label { font-size:11px; }
                    .kpi-tile .sub { font-size:10px; }
                    .t1 { background:#e6f1fb; } .t2 { background:#eaf3de; } .t3 { background:#faeeda; }
                    .t4 { background:#e8e6fb; } .t5 { background:#fbe6e6; }
                    .t6 { background:#dcf4f1; } .t7 { background:#eef2f7; }
                    .bar-box { border:1px solid #e5e7eb; border-radius:8px; padding:14px; margin-bottom:20px; }
                    .bar-box .hint { font-size:10px; color:#6b7280; margin:-4px 0 10px; }
                    .bar-row { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
                    .bar-row .name { width:130px; font-size:11px; }
                    .bar-track { flex:1; background:#f1f5f9; border-radius:4px; height:12px; }
                    .bar-fill { height:12px; border-radius:4px; }
                    .bar-row .bar-value { width:44px; font-size:11px; text-align:right; }
                    .chip-row { display:flex; flex-wrap:wrap; gap:8px; }
                    .chip { display:inline-flex; align-items:center; gap:6px; border:1px solid #e5e7eb; border-radius:20px; padding:5px 12px; font-size:11px; }
                    .pill { display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:600; }
                    .st-not-started { background:#e5e7eb; color:#374151; }
                    .st-pending { background:#dbeafe; color:#1e40af; }
                    .st-in-progress { background:#fef9c3; color:#854d0e; }
                    .st-completed { background:#dcfce7; color:#166534; }
                    .st-overdue { background:#fee2e2; color:#991b1b; }
                    .pr-high { background:#fee2e2; color:#991b1b; }
                    .pr-middle { background:#fef9c3; color:#854d0e; }
                    .pr-low { background:#e0f2fe; color:#075985; }
                    .dl-ontime { background:#dcfce7; color:#166534; }
                    .dl-late { background:#ffedd5; color:#9a3412; }
                    .dl-overdue { background:#fee2e2; color:#991b1b; }
                    .dl-upcoming { background:#dbeafe; color:#1e40af; }
                    .dl-none { background:#e5e7eb; color:#374151; }
                    .date-late { color:#991b1b; font-weight:600; }
                    .score-badge { display:inline-block; min-width:32px; padding:2px 8px; border-radius:10px; font-weight:600; }
                    .score-red { background:#fee2e2; color:#991b1b; }
                    .score-yellow { background:#fef9c3; color:#854d0e; }
                    .score-green { background:#dcfce7; color:#166534; }
                </style>
            </head>
            <body>
                <h2>KPI Performance Report</h2>
                ${content}
            </body>
            </html>
        `);
        win.document.close();
        setTimeout(() => win.print(), 300);
    }
};