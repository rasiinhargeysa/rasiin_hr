const API = "rasiin_hr.rasiin_hr.page.policy_portal.policy_portal.";
const LOGO = "/files/Alihsanhead.PNG";
const PDFJS = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/";

frappe.pages["policy-portal"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: "Policies & Procedures",
    single_column: true,
  });
  new PolicyPortal(page);
};

/*
  Three pages:
    1. show_categories()     -> every policy category with its policy count
    2. show_category(cat)    -> the policies inside one category
    3. show_policy(name)     -> a single policy (contents, links, attached file)
*/
class PolicyPortal {
  constructor(page) {
    this.page = page;
    this.cat = null; // { name, label } of the category being browsed
    this.view = "categories";
    this.add_styles();

    $(`<div class="pp-brand">
         <img src="${LOGO}" alt="Al Ihsan Specialist Hospital">
       </div>`).appendTo(page.main);

    // Search box (only visible on the category list page)
    this.search = page.add_field({
      fieldtype: "Data",
      fieldname: "search",
      label: "Search",
      change: () => {
        if (this.view === "list") this.render_list();
      },
    });

    this.root = $('<div class="pp-root"></div>').appendTo(page.main);
    this.show_categories();
  }

  // ---------- Breadcrumb ----------
  breadcrumb(policy_title) {
    const esc = frappe.utils.escape_html;
    const bar = $('<nav class="pp-crumbs"></nav>');

    $('<a href="javascript:void(0)">All categories</a>')
      .on("click", () => this.show_categories())
      .appendTo(bar);

    if (this.cat) {
      bar.append('<span class="pp-sep">/</span>');
      if (policy_title) {
        $(`<a href="javascript:void(0)">${esc(this.cat.label)}</a>`)
          .on("click", () => this.show_category(this.cat))
          .appendTo(bar);
        bar.append('<span class="pp-sep">/</span>');
        bar.append(`<span>${esc(policy_title)}</span>`);
      } else {
        bar.append(`<span>${esc(this.cat.label)}</span>`);
      }
    }
    return bar;
  }

  // ---------- Page 1: categories ----------
  async show_categories() {
    $(".pp-pop").remove();
    this.view = "categories";
    this.cat = null;
    this.page.set_title("Policies & Procedures");
    this.page.clear_secondary_action();
    $(this.page.page_form).hide();
    this.root.empty();

    const { message } = await frappe.call(API + "get_categories");
    const cats = message || [];
    const esc = frappe.utils.escape_html;

    this.root.append(`
      <h2 class="pp-title">Policy categories</h2>
      <p class="text-muted">Choose a category to see the policies inside it.</p>`);

    const grid = $('<div class="pp-cats"></div>').appendTo(this.root);
    if (!cats.length) {
      grid.html(`<div class="pp-empty text-muted">
        No policies are available yet. Please check back later.</div>`);
      return;
    }

    cats.forEach(c => {
      $(`<div class="pp-cat-card" tabindex="0" role="button">
          <div class="pp-cat-num">${c.count}</div>
          <div class="pp-cat-txt">
            <h4>${esc(c.label)}</h4>
            <span class="text-muted">${c.count} ${c.count === 1 ? "policy" : "policies"}</span>
          </div>
        </div>`)
        .on("click keydown", e => {
          if (e.type === "keydown" && e.key !== "Enter") return;
          this.show_category({ name: c.name, label: c.label });
        })
        .appendTo(grid);
    });
  }

  // ---------- Page 2: policies in one category ----------
  async show_category(cat) {
    $(".pp-pop").remove();
    this.view = "list";
    this.cat = cat;
    this.page.set_title(cat.label);
    this.page.set_secondary_action("← All categories", () => this.show_categories());
    this.search.set_value("");
    $(this.page.page_form).show();
    this.root.empty();

    const { message } = await frappe.call(API + "get_policies", { category: cat.name });
    this.policies = message || [];

    this.root.append(this.breadcrumb());
    this.root.append(`
      <h2 class="pp-title">${frappe.utils.escape_html(cat.label)}</h2>
      <p class="text-muted">${this.policies.length} ${
        this.policies.length === 1 ? "policy" : "policies"
      } in this category</p>`);

    this.list = $('<div class="pp-list"></div>').appendTo(this.root);
    this.render_list();
  }

  render_list() {
    const q = (this.search.get_value() || "").toLowerCase();
    const esc = frappe.utils.escape_html;

    const list = this.policies.filter(p =>
      `${p.title} ${p.summary || ""}`.toLowerCase().includes(q)
    );

    this.list.empty();
    if (!list.length) {
      this.list.html(`<div class="pp-empty text-muted">
        No policy matches your search. Clear the search to see all.</div>`);
      return;
    }

    list.forEach(p => {
      $(`<div class="pp-row" tabindex="0" role="button">
          <div>
            <h4>${esc(p.title)}</h4>
            <p class="text-muted">${esc(p.summary || "")}</p>
          </div>
          <small class="text-muted">${frappe.datetime.str_to_user(p.date)}</small>
        </div>`)
        .on("click keydown", e => {
          if (e.type === "keydown" && e.key !== "Enter") return;
          this.show_policy(p.name);
        })
        .appendTo(this.list);
    });
  }

  // ---------- Page 3: single policy ----------
  // jump:      heading text to scroll to after opening
  // came_from: { policy, heading } when the user arrived through a link
  async show_policy(name, jump = null, came_from = null) {
    $(".pp-pop").remove();
    const { message: d } = await frappe.call(API + "get_policy", { name });
    const esc = frappe.utils.escape_html;

    this.view = "policy";
    // Keep the breadcrumb correct even when arriving through a cross-reference
    this.cat = { name: d.category || "", label: d.category || "General" };

    $(this.page.page_form).hide();
    this.page.set_title(d.title);

    if (came_from) {
      this.page.set_secondary_action("← Back to " + came_from.heading, () =>
        this.show_policy(came_from.policy, came_from.heading)
      );
    } else {
      this.page.set_secondary_action("← " + this.cat.label, () => this.show_category(this.cat));
    }

    this.root.html(`
      <div class="pp-bc"></div>
      <div class="pp-doc">
        <nav class="pp-toc"><b>On this page</b><div class="pp-toc-links"></div></nav>
        <article class="pp-article">
          <span class="pp-chip">${esc(d.category || "General")}</span>
          <h1>${esc(d.title)}</h1>
          <p class="text-muted">Effective ${frappe.datetime.str_to_user(d.date)}</p>
          <div class="pp-body ql-editor read-mode">${d.content || ""}</div>
          ${
            d.has_file
              ? `<h3 class="pp-file-title">Attached document</h3>
                 <div class="pp-file text-muted">Loading document...</div>`
              : ""
          }
        </article>
      </div>`);

    this.root.find(".pp-bc").append(this.breadcrumb(d.title));

    const toc = this.root.find(".pp-toc-links");
    this.root.find(".pp-body h2, .pp-body h3").each((i, h) => {
      h.id = "sec-" + i;
      toc.append(
        `<a class="${h.tagName === "H3" ? "sub" : ""}" href="javascript:void(0)"
            onclick="document.getElementById('sec-${i}').scrollIntoView({behavior:'smooth'})">
            ${esc(h.textContent)}</a>`
      );
    });
    if (!toc.children().length) this.root.find(".pp-toc").hide();

    if (d.has_file) this.load_file(name);

    this.current_policy = name;
    window.scrollTo(0, 0);
    this.setup_links(name, jump);
  }

  // ---------- Cross-references between sections ----------
  async setup_links(policy, jump) {
    const { message } = await frappe.call(API + "get_links", { policy });
    const links = (message && message.links) || [];
    const can_edit = !!(message && message.can_edit);
    const norm = s => (s || "").replace(/\s+/g, " ").trim();

    this.root.find(".pp-xref").remove();
    const by = {};
    links.forEach(l => (by[l.heading] = by[l.heading] || []).push(l));

    let target = null;
    this.root.find(".pp-body h2, .pp-body h3, .pp-body h4").each((i, h) => {
      const t = norm(h.textContent);
      if (jump && t === jump && !target) target = h;

      const mine = by[t] || [];
      if (!mine.length && !can_edit) return;

      $(`<a class="pp-xref" href="javascript:void(0)">
           ${mine.length ? "Related (" + mine.length + ")" : "+ Link"}</a>`)
        .attr("contenteditable", "false")
        .on("click", e => {
          e.stopPropagation();
          this.links_popover(e.currentTarget, policy, t, mine, can_edit);
        })
        .appendTo(h);
    });

    $(document)
      .off("mousedown.ppx")
      .on("mousedown.ppx", e => {
        if (!$(e.target).closest(".pp-pop, .pp-xref").length) $(".pp-pop").remove();
      });

    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      $(target).addClass("pp-flash");
      setTimeout(() => $(target).removeClass("pp-flash"), 2500);
    }
  }

  links_popover(el, policy, heading, mine, can_edit) {
    $(".pp-pop").remove();
    const esc = frappe.utils.escape_html;

    const pop = $(`<div class="pp-pop">
        ${mine
          .map(
            l => `
          <div class="pp-xref-row">
            <a href="javascript:void(0)" class="pp-go" data-p="${esc(l.to_policy)}" data-h="${esc(l.to_heading)}">
              <b>${esc(l.to_title)}</b><br><small>${esc(l.to_heading)}</small></a>
            ${
              can_edit
                ? `<a href="javascript:void(0)" class="pp-rm text-danger small" data-n="${esc(l.name)}">Remove</a>`
                : ""
            }
          </div>`
          )
          .join("")}
        ${can_edit ? `<a href="javascript:void(0)" class="pp-add small">+ Link to another section</a>` : ""}
      </div>`).appendTo("body");

    const r = el.getBoundingClientRect();
    pop.css({
      position: "fixed",
      top: r.bottom + 6,
      left: Math.max(8, Math.min(r.left, window.innerWidth - 300)),
      zIndex: 1050,
    });

    pop.find(".pp-go").on("click", e => {
      const { p, h } = e.currentTarget.dataset;
      pop.remove();
      this.show_policy(p, h, { policy, heading });
    });

    pop.find(".pp-rm").on("click", async e => {
      await frappe.call(API + "delete_link", { name: e.currentTarget.dataset.n });
      pop.remove();
      this.setup_links(policy, null);
    });

    pop.find(".pp-add").on("click", () => {
      pop.remove();
      this.link_dialog(policy, heading);
    });
  }

  link_dialog(policy, heading) {
    const d = new frappe.ui.Dialog({
      title: "Link section",
      fields: [
        { fieldtype: "Data", fieldname: "this_section", label: "This section", default: heading, read_only: 1 },
        {
          fieldtype: "Link",
          fieldname: "target_policy",
          label: "Policy to link to",
          options: "HR Policy",
          reqd: 1,
          get_query: () => ({ filters: { published: 1 } }),
          change: async () => {
            const p = d.get_value("target_policy");
            if (!p) return;
            const { message } = await frappe.call(API + "get_headings", { policy: p });
            d.set_df_property("target_heading", "options", ["", ...(message || [])].join("\n"));
            d.set_value("target_heading", "");
          },
        },
        { fieldtype: "Select", fieldname: "target_heading", label: "Section", reqd: 1 },
      ],
      primary_action_label: "Save link",
      primary_action: async v => {
        await frappe.call({
          method: API + "add_link",
          args: {
            policy,
            heading,
            target_policy: v.target_policy,
            target_heading: v.target_heading,
          },
        });
        d.hide();
        frappe.show_alert({ message: "Link saved", indicator: "green" });
        this.setup_links(policy, null);
      },
    });
    d.show();
  }

  // ---------- View-only document ----------
  async load_pdfjs() {
    if (window.pdfjsLib) return window.pdfjsLib;
    await new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = PDFJS + "pdf.min.js";
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS + "pdf.worker.min.js";
    return window.pdfjsLib;
  }

  async load_file(name) {
    const box = this.root.find(".pp-file");
    try {
      const { message: f } = await frappe.call(API + "get_policy_file", { name });
      const bytes = Uint8Array.from(atob(f.data), c => c.charCodeAt(0));

      box.empty().removeClass("text-muted");
      box.on("contextmenu", e => e.preventDefault()); // block right-click save

      if (f.mime.startsWith("image/")) {
        const url = URL.createObjectURL(new Blob([bytes], { type: f.mime }));
        box.html(`<img src="${url}" draggable="false" alt="Policy document">`);
        return;
      }

      if (f.mime !== "application/pdf") {
        box.html("This file type cannot be previewed. Please ask HR to upload a PDF.");
        return;
      }

      try {
        // Preferred: draw pages ourselves (no browser toolbar, no download button)
        const pdfjsLib = await this.load_pdfjs();
        const pdf = await pdfjsLib.getDocument({ data: bytes.slice() }).promise;
        const width = box.width() || 800;
        for (let n = 1; n <= pdf.numPages; n++) {
          const pg = await pdf.getPage(n);
          const base = pg.getViewport({ scale: 1 });
          const vp = pg.getViewport({ scale: (width / base.width) * (window.devicePixelRatio || 1) });
          const canvas = document.createElement("canvas");
          canvas.width = vp.width;
          canvas.height = vp.height;
          canvas.className = "pp-page";
          box.append(canvas);
          await pg.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
        }
      } catch (e) {
        console.error("PDF.js failed, using fallback viewer", e);
        const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
        box.html(`<iframe src="${url}#toolbar=0&navpanes=0" title="Policy document"></iframe>`);
      }
    } catch (e) {
      console.error(e);
      const msg = (e && e.message) || (e && e._server_messages) || "";
      box.html(`The attached document could not be loaded.
                <div class="small">${frappe.utils.escape_html(String(msg))}</div>`);
    }
  }

  // ---------- Styles (Al Ihsan brand, white page) ----------
  add_styles() {
    $("#pp-style").remove();
    $(`<style id="pp-style">
      /* Card borders */
      :root{--pp-edge:#c3cbe3}
      [data-theme="dark"]{--pp-edge:#3a4670}

      /* White page background */
      body[data-route="policy-portal"],
      [data-page-route="policy-portal"],
      [data-page-route="policy-portal"] .page-body{background:var(--fg-color,#fff) !important}

      .pp-brand{background:#fff;border:2px solid var(--pp-edge);border-radius:12px;padding:16px 24px;margin-top:12px;text-align:center}
      .pp-brand img{width:100%;max-width:700px;height:auto;display:inline-block}
      @media(max-width:600px){.pp-brand{padding:10px 12px}}

      .pp-title{color:#0a1f6b;margin:22px 0 2px}
      .pp-crumbs{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:18px;font-size:13px;color:var(--text-muted)}
      .pp-crumbs a{color:#1a3fb0;text-decoration:underline;text-underline-offset:3px}
      .pp-sep{opacity:.5}
      .pp-bc .pp-crumbs{margin-top:16px}

      /* Page 1: categories */
      .pp-cats,.pp-list{display:grid;gap:16px;grid-template-columns:repeat(4,minmax(0,1fr));margin-top:16px}
      @media(max-width:1100px){.pp-cats,.pp-list{grid-template-columns:repeat(3,minmax(0,1fr))}}
      @media(max-width:820px){.pp-cats,.pp-list{grid-template-columns:repeat(2,minmax(0,1fr))}}
      @media(max-width:520px){.pp-cats,.pp-list{grid-template-columns:1fr}}
      .pp-empty{grid-column:1/-1;padding:40px;text-align:center}
      .pp-cat-card{display:flex;flex-direction:column;align-items:stretch;background:var(--card-bg);border:2px solid var(--pp-edge);border-radius:14px;overflow:hidden;cursor:pointer;transition:.15s}
      .pp-cat-card:hover,.pp-cat-card:focus{transform:translateY(-3px);box-shadow:var(--shadow-lg);outline:none}
      .pp-cat-num{flex:0 0 96px;display:grid;place-items:center;background:linear-gradient(160deg,#0a1f6b,#1a3fb0);color:#fff;font-size:44px;font-weight:700;transition:.15s}
      .pp-cat-card:hover .pp-cat-num,.pp-cat-card:focus .pp-cat-num{background:linear-gradient(160deg,#e8660c,#f39a1f)}
      .pp-cat-txt{padding:18px}
      .pp-cat-txt h4{margin:0 0 4px;color:#0a1f6b}

      /* Page 2: policy list */
            .pp-row{display:flex;flex-direction:column;justify-content:space-between;align-items:flex-start;gap:12px;background:var(--card-bg);border:2px solid var(--pp-edge);border-top:4px solid #f39a1f;border-radius:12px;padding:18px 18px 14px;cursor:pointer;transition:.15s}
      .pp-row:hover,.pp-row:focus{transform:translateY(-3px);border-top-color:#e8660c;box-shadow:var(--shadow-lg);outline:none}
      .pp-row h4{margin:0 0 3px;color:#0a1f6b}
      .pp-row p{margin:0;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
      .pp-row small{white-space:nowrap}

      /* Page 3: single policy */
      .pp-doc{display:grid;grid-template-columns:230px 1fr;gap:28px;align-items:start;margin-top:16px}
      @media(max-width:800px){.pp-doc{grid-template-columns:1fr}}
      .pp-toc{position:sticky;top:80px;background:var(--card-bg);border:2px solid var(--pp-edge);border-left:4px solid #f39a1f;border-radius:12px;padding:16px;font-size:13px}
      .pp-toc b{color:#0a1f6b}
      .pp-toc a{display:block;padding:4px 0;color:var(--text-muted)}
      .pp-toc a:hover{color:#e8660c}
      .pp-toc a.sub{padding-left:14px}
      .pp-article{background:var(--card-bg);border:2px solid var(--pp-edge);border-radius:12px;padding:28px 34px;min-width:0}
      .pp-chip{display:inline-block;font-size:12px;font-weight:600;background:#fff1c9;color:#8a4b00;border-radius:999px;padding:2px 12px}
      .pp-article h1{color:#0a1f6b}
      .pp-body{padding:0 !important}
      .pp-body h2{margin-top:28px;color:#0a1f6b;border-bottom:2px solid #f39a1f;padding-bottom:6px}
      .pp-body h3{color:#1a3fb0}
      .pp-body table{width:100%;display:block;overflow-x:auto}
      .pp-body th{background:#0a1f6b;color:#fff}

      /* Cross-reference badges and popup */
      .pp-xref{font-size:11px;font-weight:500;margin-left:10px;padding:2px 8px;border-radius:10px;background:#fff1c9;color:#8a4b00 !important;border:1px solid #f39a1f;vertical-align:middle;white-space:nowrap;cursor:pointer;user-select:none}
      .pp-xref:hover{background:#f39a1f;color:#fff !important;text-decoration:none}
      .pp-pop{width:280px;background:var(--card-bg);border:2px solid var(--pp-edge);border-left:4px solid #f39a1f;border-radius:10px;padding:12px 14px;box-shadow:var(--shadow-lg);font-size:13px}
      .pp-xref-row{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px solid var(--border-color)}
      .pp-xref-row:last-of-type{margin-bottom:8px}
      .pp-flash{animation:ppflash 2.5s}
      @keyframes ppflash{0%,60%{background:#fff1c9}100%{background:transparent}}

      /* Attached document (view only) */
      .pp-file-title{margin-top:28px;color:#0a1f6b}
      .pp-file{user-select:none;-webkit-user-select:none}
      .pp-file .pp-page{display:block;width:100%;height:auto;margin-bottom:12px;border:1px solid var(--border-color);border-radius:6px;background:#fff}
      .pp-file iframe{width:100%;height:80vh;border:1px solid var(--border-color);border-radius:8px}
      .pp-file img{max-width:100%;border:1px solid var(--border-color);border-radius:8px;-webkit-user-drag:none}
      @media print{.pp-file{display:none !important}.pp-xref{display:none !important}}

      [data-theme="dark"] .pp-title,
      [data-theme="dark"] .pp-cat-txt h4,
      [data-theme="dark"] .pp-row h4,
      [data-theme="dark"] .pp-article h1,
      [data-theme="dark"] .pp-toc b,
      [data-theme="dark"] .pp-file-title,
      [data-theme="dark"] .pp-body h2{color:#9db4ff}
      [data-theme="dark"] .pp-body h3,
      [data-theme="dark"] .pp-crumbs a{color:#8aa6ff}
      [data-theme="dark"] .pp-flash{color:#111}
    </style>`).appendTo("head");
  }
}