import base64
import html
import mimetypes

import frappe
from bs4 import BeautifulSoup
from frappe import _
from frappe.utils import strip_html

XREF = "Policy Cross Reference"


# ---------------------------------------------------------------- helpers
def check_access():
    """Any logged-in user can read policies."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in"), frappe.PermissionError)


def is_hr():
    return "HR Manager" in frappe.get_roles()


def norm(s):
    return " ".join((s or "").split())


def attachment_field():
    """Find the Attach field, whatever its fieldname is (attachment / atachment ...)."""
    for df in frappe.get_meta("HR Policy").fields:
        if df.fieldtype in ("Attach", "Attach Image"):
            return df.fieldname
    return None


def get_published_policy(name):
    check_access()
    if not frappe.db.exists("HR Policy", {"name": name, "published": 1}):
        frappe.throw(_("Policy not found"), frappe.DoesNotExistError)
    return frappe.get_doc("HR Policy", name)


def headings_of(content):
    """Unique heading texts (h2, h3, h4) found in the policy content."""
    soup = BeautifulSoup(content or "", "html.parser")
    out = []
    for h in soup.find_all(["h2", "h3", "h4"]):
        t = norm(h.get_text())
        if t and t not in out:
            out.append(t)
    return out


# ---------------------------------------------------------------- categories
@frappe.whitelist()
def get_categories():
    """Page 1: every category that has published policies, with its policy count.

    `name` is the raw category value ("" when a policy has no category),
    `label` is what the page shows ("General" for an empty category).
    """
    check_access()
    rows = frappe.get_all(
        "HR Policy",
        filters={"published": 1},
        fields=["policy_category", "count(name) as count"],
        group_by="policy_category",
        order_by="policy_category asc",
    )

    # Merge NULL and "" into one "General" group
    merged = {}
    for r in rows:
        key = r.policy_category or ""
        merged[key] = merged.get(key, 0) + r["count"]

    return [
        {"name": key, "label": key or "General", "count": count}
        for key, count in sorted(merged.items(), key=lambda kv: (kv[0] == "", kv[0].lower()))
    ]


# ---------------------------------------------------------------- policies
@frappe.whitelist()
def get_policies(category=None):
    """Page 2: published policies. Pass `category` to list one category only.

    An empty string means the "General" group (policies without a category).
    """
    check_access()

    filters = {"published": 1}
    if category is not None:
        if category == "":
            filters["policy_category"] = ["is", "not set"]
        else:
            filters["policy_category"] = category

    policies = frappe.get_all(  # server-side read: no Desk permission needed
        "HR Policy",
        filters=filters,
        fields=["name", "title", "policy_category", "date", "content"],
        order_by="title asc",
    )
    for p in policies:
        text = " ".join(html.unescape(strip_html(p.pop("content") or "")).split())
        p["summary"] = text[:140] + ("…" if len(text) > 140 else "")
    return policies


@frappe.whitelist()
def get_policy(name):
    """Page 3: one policy."""
    doc = get_published_policy(name)
    field = attachment_field()
    return {
        "name": doc.name,
        "title": doc.title,
        "category": doc.policy_category,
        "date": doc.date,
        "content": doc.content,
        "has_file": bool(field and doc.get(field)),  # file address is NOT sent
    }


@frappe.whitelist()
def get_policy_file(name):
    """Return the attached file for in-page viewing only."""
    doc = get_published_policy(name)
    field = attachment_field()
    file_url = field and doc.get(field)
    if not file_url:
        frappe.throw(_("This policy has no attached file"))

    file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    if not file_name:
        frappe.throw(_("File record not found: {0}").format(file_url))

    file_doc = frappe.get_doc("File", file_name)
    content = file_doc.get_content()
    if isinstance(content, str):
        content = content.encode()

    return {
        "mime": mimetypes.guess_type(file_doc.file_name or file_url)[0] or "application/octet-stream",
        "data": base64.b64encode(content).decode(),
    }


# ---------------------------------------------------------------- cross references
@frappe.whitelist()
def get_headings(policy):
    return headings_of(get_published_policy(policy).content)


@frappe.whitelist()
def get_links(policy):
    """All links that start or end in this policy, shown from this policy's side."""
    get_published_policy(policy)
    rows = frappe.get_all(
        XREF,
        or_filters=[["source_policy", "=", policy], ["target_policy", "=", policy]],
        fields=["name", "source_policy", "source_heading", "target_policy", "target_heading"],
    )

    links = []
    for r in rows:
        pairs = []
        if r.source_policy == policy:
            pairs.append((r.source_heading, r.target_policy, r.target_heading))
        if r.target_policy == policy:
            pairs.append((r.target_heading, r.source_policy, r.source_heading))

        for heading, other, other_heading in pairs:
            title, published = frappe.db.get_value("HR Policy", other, ["title", "published"]) or (None, 0)
            if not published:
                continue
            links.append({
                "name": r.name,
                "heading": heading,
                "to_policy": other,
                "to_title": title,
                "to_heading": other_heading,
            })
    return {"links": links, "can_edit": is_hr()}


@frappe.whitelist()
def add_link(policy, heading, target_policy, target_heading):
    if not is_hr():
        frappe.throw(_("Only HR can add links"), frappe.PermissionError)

    src = get_published_policy(policy)
    tgt = get_published_policy(target_policy)
    heading, target_heading = norm(heading), norm(target_heading)

    if heading not in headings_of(src.content) or target_heading not in headings_of(tgt.content):
        frappe.throw(_("Section not found"))
    if policy == target_policy and heading == target_heading:
        frappe.throw(_("A section cannot be linked to itself"))

    # same pair in either direction already exists?
    for a, ah, b, bh in (
        (policy, heading, target_policy, target_heading),
        (target_policy, target_heading, policy, heading),
    ):
        if frappe.db.exists(XREF, {
            "source_policy": a, "source_heading": ah,
            "target_policy": b, "target_heading": bh,
        }):
            frappe.throw(_("These sections are already linked"))

    doc = frappe.get_doc({
        "doctype": XREF,
        "source_policy": policy,
        "source_heading": heading,
        "target_policy": target_policy,
        "target_heading": target_heading,
    })
    doc.insert(ignore_permissions=True)
    return doc.name


@frappe.whitelist()
def delete_link(name):
    if not is_hr():
        frappe.throw(_("Only HR can remove links"), frappe.PermissionError)
    frappe.delete_doc(XREF, name, ignore_permissions=True)


def delete_policy_links(doc, method=None):
    """Hook: remove a policy's links when the policy is deleted (see hooks.py)."""
    for n in frappe.get_all(
        XREF,
        or_filters=[["source_policy", "=", doc.name], ["target_policy", "=", doc.name]],
        pluck="name",
    ):
        frappe.delete_doc(XREF, n, ignore_permissions=True, force=True)