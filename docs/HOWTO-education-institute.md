# HOW-TO: Modelling an Education Institute in Topaz via Frappe

Audience: an app developer building an education-style Frappe app (e.g.
`edu_institute`) on top of `topaz_frappe`.

**One authoring path: Frappe.** You model the institute as ordinary Frappe
documents and `topaz_frappe` writes the objects and relationship tuples into
Topaz. There is no seed-file workflow and no JSON shipped in this repo — the
only data source is your Frappe database, synced to Topaz.

The **Topaz schema** (object types + relation definitions + computed
permissions) is *your Topaz deployment's* concern; it lives in that deployment's
single `manifest.yaml`, not in this app. This document shows only the subset of
relations your Frappe app must emit so the institute model resolves correctly.

Reference relations your app will emit (Frappe writes the **base** relations;
Topaz composes the `/permissions`):

| Type | Base relations (emitted by Frappe) | Computed perms (in manifest.yaml) |
| --- | --- | --- |
| `user` | – | – |
| `identity` | `identifier: user` | – |
| `group` | `member: user \| group#member` | – |
| `role` | `assignee: user \| group#member` | `is_assigned = assignee` |
| `institution` | `director`, `deputy_director`, `dean_rnd`, `dean_ap`, `dean_admin`, `registrar`, `central_audit: user\|role#assignee`, `alumni` | `is_executive = director\|deputy_director`, `is_central_admin = registrar\|central_audit`, `can_escalate_rnd = dean_rnd\|director` |
| `department` | `parent_institute: institution`, `head`, `acting_head`, `faculty: user\|group#member`, `adjunct_faculty`, `postdoc`, `student: user\|group#member`, `store_keeper: user\|role#assignee`, `staff: user\|group#member`, `candidate: user\|group#member` | `lead = head\|acting_head`, `is_academic`, `is_member`, `is_applicant`, `can_escalate_rnd`, `can_audit` |
| `committee` | `chair`, `member: user\|group#member`, `external_expert` | `is_member`, `can_review`, `can_sign` |
| `project` | `host_department: department`, `lead_investigator`, `co_investigator`, `research_fellow`, `delegate_signatory` | `investigator`, `lead`, `member`, `department_lead`, `can_view` |

The key idea: **the Topaz `/permissions` (computed relations) live in the Topaz
schema. Frappe only writes the base relations.** `topaz_frappe` never re-implements
`is_member`/`can_view` etc. — it maps documents to the base tuples and lets Topaz
answer `Check` for the computed ones.

---

## 0. Prerequisites

```bash
# on the site
bench --site my-site set-config topaz_enabled true
bench --site my-site set-config topaz_host http://localhost:9292
bench --site my-site set-config topaz_subject_type user
```

You also need the Topaz schema loaded in your Topaz deployment (its single
`manifest.yaml`) with the object types/relations listed above. That is a Topaz
deployment concern, not a Frappe one. **This repo ships no schema file and no
seed data** — align your `manifest.yaml` with the relation names your app emits
(below).

---

## 1. Model the documents in Frappe

Create ordinary DocTypes (Frappe UI or fixtures). The education app owns these.

### Institution
DocType `Institute`:
- `name` (autoname, e.g. `IITB`)
- Link `director` → `User`
- Link `deputy_director` → `User`
- Link `dean_rnd` → `User`
- Link `dean_ap` → `User`
- Link `dean_admin` → `User`
- Link `registrar` → `User`

### Department
DocType `Department`:
- `name` (autoname, e.g. `CSE`)
- Link `parent_institute` → `Institute`
- Link `head` → `User`
- Link `acting_head` → `User`
- Table `faculty_members` (child `Department Member`): `user` Link→User
- Table `students` (child `Department Member`): `user` Link→User
- Table `staff_members` (child `Department Member`): `user` Link→User
- Table `candidates` (child `Department Member`): `user` Link→User
- Link `store_keeper_role` → `Role`

### Committee
DocType `Committee`:
- `name`
- Link `chair` → `User`
- Table `members` (child `Committee Member`): `user` Link→User
- Table `external_experts` (child `Committee Member`): `user` Link→User

### Project
DocType `Project`:
- `name`
- Link `host_department` → `Department`
- Link `lead_investigator` → `User`
- Link `co_investigator` → `User`
- Link `research_fellow` → `User`
- Link `delegate_signatory` → `User`

### Group / Role helpers (these map to Topaz `group` / `role`)
DocType `Topaz Group`:
- `name` (e.g. `faculty`, `staff`, `students`, `candidates`, `developers`)
- Table `members` (child `Topaz Group Member`): `user` Link→User

DocType `Topaz Role`:
- `name` (e.g. `system_admin`, `internal_auditor`, `store_manager`, `hod`)
- Table `assignees` (child `Topaz Role Assignee`): `user` Link→User
- (optionally `group` Link→Topaz Group for `group#member` assignees)

> Each Topaz object's `id` is the Frappe document `name` (via `id_field: name`).
> Choose names that are meaningful and stable (e.g. `IITB`, `CSE`); they become
> the ids in Topaz.

---

## 2. Declare the mapping in your app's `hooks.py`

This is the whole integration. **`topaz_frappe` ships no education types** —
your app declares them.

```python
# edu_institute/hooks.py

topaz_registry = {
    "Institute": {
        "object": {"type": "institution", "id_field": "name"},
        "relations": [
            {"kind": "link", "relation": "director",        "target_type": "user", "field": "director"},
            {"kind": "link", "relation": "deputy_director", "target_type": "user", "field": "deputy_director"},
            {"kind": "link", "relation": "dean_rnd",        "target_type": "user", "field": "dean_rnd"},
            {"kind": "link", "relation": "dean_ap",         "target_type": "user", "field": "dean_ap"},
            {"kind": "link", "relation": "dean_admin",      "target_type": "user", "field": "dean_admin"},
            {"kind": "link", "relation": "registrar",       "target_type": "user", "field": "registrar"},
        ],
    },

    "Department": {
        "object": {"type": "department", "id_field": "name"},
        "relations": [
            # Edge to the parent institution (object -> object)
            {"kind": "link", "relation": "parent_institute",
             "target_type": "institution", "field": "parent_institute"},

            {"kind": "link", "relation": "head",        "target_type": "user", "field": "head"},
            {"kind": "link", "relation": "acting_head", "target_type": "user", "field": "acting_head"},

            # Child-table memberships
            {"kind": "child", "table": "faculty_members",
             "subject_field": "user", "static_relation": "faculty"},
            {"kind": "child", "table": "students",
             "subject_field": "user", "static_relation": "student"},
            {"kind": "child", "table": "staff_members",
             "subject_field": "user", "static_relation": "staff"},
            {"kind": "child", "table": "candidates",
             "subject_field": "user", "static_relation": "candidate"},

            # store_keeper points at a ROLE via a subject-set (role:X#assignee)
            {"kind": "link", "relation": "store_keeper",
             "target_type": "role", "field": "store_keeper_role",
             "subject_relation": "assignee"},
        ],
    },

    "Committee": {
        "object": {"type": "committee", "id_field": "name"},
        "relations": [
            {"kind": "link", "relation": "chair", "target_type": "user", "field": "chair"},
            {"kind": "child", "table": "members",
             "subject_field": "user", "static_relation": "member"},
            {"kind": "child", "table": "external_experts",
             "subject_field": "user", "static_relation": "external_expert"},
        ],
    },

    "Project": {
        "object": {"type": "project", "id_field": "name"},
        "relations": [
            {"kind": "link", "relation": "host_department",
             "target_type": "department", "field": "host_department"},
            {"kind": "link", "relation": "lead_investigator",
             "target_type": "user", "field": "lead_investigator"},
            {"kind": "link", "relation": "co_investigator",
             "target_type": "user", "field": "co_investigator"},
            {"kind": "link", "relation": "research_fellow",
             "target_type": "user", "field": "research_fellow"},
            {"kind": "link", "relation": "delegate_signatory",
             "target_type": "user", "field": "delegate_signatory"},
        ],
    },

    "Topaz Group": {
        "object": {"type": "group", "id_field": "name"},
        "relations": [
            {"kind": "child", "table": "members",
             "subject_field": "user", "static_relation": "member"},
        ],
    },

    "Topaz Role": {
        "object": {"type": "role", "id_field": "name"},
        "relations": [
            {"kind": "child", "table": "assignees",
             "subject_field": "user", "static_relation": "assignee"},
            # optional: group-based role assignment -> role:X#assignee@group:Y#member
            {"kind": "child", "table": "group_assignees",
             "subject_field": "group", "static_relation": "assignee",
             "default_subject_type": "group", "subject_relation": "member"},
        ],
    },
}
```

Notes:

- `subject_relation: "assignee"` produces `… #store_keeper @ role:store_manager #assignee`
  (a subject-set: the role's assignees).
- `default_subject_type: "group"` + `subject_relation: "member"` on the role's
  `group_assignees` produces `role:course_instructor#assignee@group:faculty#member`.
- `group.member: user | group#member` recursion is defined in your `manifest.yaml`;
  if you allow nested groups, the `Topaz Group` child can carry a `group` link
  with `subject_relation: "member"` (same pattern as the role).

---

## 3. Users, identities

An `identity` object lets an email or an institute id map to the same `user`
(e.g. `identity:faculty@example.org#identifier@user:faculty`). This is optional
but useful when users have multiple identifiers.

Register the `User` DocType so each user is a `user` object, and add a small
`Topaz Identity` DocType (or a child table on `User`) whose rows emit
`identity:<id>#identifier@user:<user>`.

```python
topaz_registry.update({
    "User": {
        "object": {"type": "user", "id_field": "name"},
        "relations": [],          # identity edges come from Topaz Identity below
    },
    "Topaz Identity": {
        "object": {"type": "identity", "id_field": "name"},
        "relations": [
            {"kind": "link", "relation": "identifier",
             "target_type": "user", "field": "user"},
        ],
    },
})
```

- Frappe `User.name` is the email. If you set the Topaz `user` id to the *short*
  id (`faculty`) instead of email, use `id_transform` or a custom `user` id
  field, and keep the email as an `identity` row.

---

## 4. Where the "permissions" go

Do **not** put `is_executive`, `is_member`, `can_view`, `lead`, etc. in Frappe.
They are computed relations defined in your Topaz `manifest.yaml`. Frappe only
emits the base relations your documents carry (`director`, `faculty`,
`parent_institute`, …). When something calls `Check` for
`department:CSE#is_member@user:faculty`, Topaz composes it.

If you later want Frappe Desk permissions answered from Topaz (opt-in), map the
*read* permission to whichever computed relation grants list visibility, e.g.:

```python
"Department": {
    "object": {"type": "department", "id_field": "name"},
    "relations": [ ...as above... ],
    # only used when topaz_desk_permissions = true
    "permissions": {"read": "is_member"},
}
```

Requires: `bench --site my-site set-config topaz_desk_permissions true`.

---

## 5. Sync the data — Frappe is the source

Create your Institution/Department/Committee/Project documents in Frappe as
usual; they sync to Topaz automatically after commit, as a delta. For a first
backfill (or after enabling sync on an existing DB), run:

```bash
# backfill every managed DocType declared above
bench --site my-site topaz-sync

# or one at a time, in dependency order
bench --site my-site topaz-sync --doctype "Topaz Group"
bench --site my-site topaz-sync --doctype "Topaz Role"
bench --site my-site topaz-sync --doctype Institute
bench --site my-site topaz-sync --doctype Department
bench --site my-site topaz-sync --doctype Committee
bench --site my-site topaz-sync --doctype Project
```

There is no separate seed file to load: the Frappe records **are** the source of
truth for these objects. Check health:

```bash
bench --site my-site topaz-health --object-type department --object-id CSE --relation is_member
```

---

## 6. Worked example — one department

Frappe document:

```
Department  name=CSE
  parent_institute = IITB
  head = faculty
  faculty_members = [faculty, faculty2]
  students = [student]
  staff_members = [staff]
  store_keeper_role = store_manager
```

Produces these Topaz tuples (plus `department:CSE` object):

```
department:CSE#parent_institute@institution:IITB
department:CSE#head@user:faculty
department:CSE#faculty@user:faculty
department:CSE#faculty@user:faculty2
department:CSE#student@user:student
department:CSE#staff@user:staff
department:CSE#store_keeper@role:store_manager#assignee
```

Topaz can then answer `department:CSE#is_member@user:student` → true
(via `is_academic ⊃ student`), and `department:CSE#can_audit@user:tester` →
true if `tester` is assignee of `internal_auditor` and IITB has
`central_audit=role:internal_auditor#assignee`.

---

## 7. Checklist

- [ ] DocTypes created (Institute, Department, Committee, Project, Topaz Group, Topaz Role, Topaz Identity).
- [ ] `topaz_registry` added to **your** app's `hooks.py`.
- [ ] `topaz_enabled` set; `topaz_host` reachable.
- [ ] Your Topaz deployment's `manifest.yaml` defines the object types/relations your app emits (this repo ships no schema).
- [ ] `user` ids consistent between Frappe and Topaz (email vs short id — pick one).
- [ ] Base relations only in Frappe; computed `/permissions` only in Topaz.
- [ ] `bench --site my-site topaz-sync` run; `topaz-health` probe passes.
- [ ] (Optional) `topaz_desk_permissions true` + `"permissions": {"read": ...}` per DocType.

---

## 8. Gotchas

- **Ids.** `id_field: name` means the Frappe name *is* the Topaz id
  (`dept_cse`, not `department:CSE:CSE`). Keep them clean and stable; renaming a
  doc changes its id (the engine will create the new object and prune is a
  separate reconcile).
- **Subject sets** need `subject_relation` (e.g. `#assignee`, `#member`). Forget
  it and you get a direct `@role:store_manager` instead of `@role:store_manager#assignee`.
- **Link to a Role/Group** uses `target_type: "role"`/`"group"`, not `"user"`.
- **Child table relations** require either `static_relation` or `relation_field`.
- **`managed`** stays default `True` for these — Frappe owns them. Mark
  `managed: False` only for object types some *other* service writes directly.
- **Deletes cascade** in Topaz (object delete drops its tuples).
