### Topaz Frappe

Topaz Authorization for Frappe.

`topaz_frappe` turns Frappe into the **authoring and management surface for a
[Topaz](https://www.topaz.sh) (AuthZen-compatible) directory**. You model
objects and relationships as normal Frappe documents; this library syncs them
into Topaz. Topaz in turn serves [AuthZen](https://openid.net/wg/authzen/)
decisions to any number of downstream microservices — it is the source of truth
for authorization, not Frappe.

- **Configure once per site** — enable sync, optionally enable Desk permission
  enforcement (off by default).
- **Declare mappings per app** — the library ships **no** domain types. Each
  consuming app (an education ERP, a corporate tool, …) registers its own
  object types and relations via a `topaz_registry` hook.
- **Background, commit-safe sync** — Frappe documents become Topaz objects and
  relationship tuples after the DB transaction commits, as a minimal delta (no
  delete-all churn).
- **Opt-in Desk permissions** — apps that want Frappe's own `has_permission` /
  list filters answered from Topaz enable `topaz_desk_permissions`. Off by
  default; when off, Frappe's native permission system is untouched.

### Sample Reference App

For a complete working reference application modeling an educational institute hierarchy (Institutes, Departments, Committees, Projects, Roles, Groups, and Users) integrated with Topaz, see:
- [**`castlecraft/eduauth_frappe`**](https://github.com/castlecraft/eduauth_frappe)

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app topaz_frappe
```

### Quick start

1. Enable sync in your site config:

   ```bash
   bench --site my-site set-config topaz_enabled true
   bench --site my-site set-config topaz_host http://localhost:8383
   ```

2. In your **consuming app's** `hooks.py`, declare the DocTypes you want in
   Topaz:

   ```python
   topaz_registry = {
       "Committee": {
           "object": {"type": "committee", "id_field": "name"},
           "relations": [
               {"kind": "link", "relation": "owner",
                "target_type": "user", "field": "owner"},
               {"kind": "child", "table": "members",
                "subject_field": "user", "static_relation": "member"},
           ],
       },
   }
   ```

3. Create/update documents as usual — they sync to Topaz automatically. Run a
   bulk backfill any time:

   ```bash
   bench --site my-site topaz-sync
   ```

### Site Configuration Reference

All settings live in the site's `site_config.json` (set via `bench --site <site> set-config <key> <value>`). The library degrades to a no-op when Topaz is disabled.

| Key | Default | Meaning |
| --- | --- | --- |
| `topaz_enabled` | `false` | Master switch for Topaz sync. When off, all sync hooks are no-ops. |
| `topaz_host` | `http://localhost:8383` | Topaz directory REST base URL. |
| `topaz_api_key` | – | Optional bearer token sent to Topaz. |
| `topaz_sync_queue` | `short` | RQ queue for background sync jobs. |
| `topaz_subject_type` | `user` | Topaz object type used for Frappe users. |
| `topaz_perm_cache_enabled` | `true` | Per-request memoisation of permission checks. |
| `topaz_inflight_threshold` | `5000` | Chunk size for `IN (...)` predicates in list filters. |
| `topaz_desk_permissions` | `false` | **Opt-in.** Enforce Frappe Desk permissions (`has_permission` & list filters) from Topaz. |

### CLI Commands

* `bench --site <site> topaz-health` — Connectivity, latency, and sample authorization probe.
* `bench --site <site> topaz-sync` — Bulk (re)sync database records into Topaz.
* `bench --site <site> topaz-load-manifest` — Load and apply schema manifest YAML into Topaz.
* `bench --site <site> topaz-prune` — Reconcile Topaz directory and prune dangling objects missing from Frappe.

### Opt-in Desk Permissions

By default, `topaz_frappe` authors documents in Frappe and streams them to Topaz, leaving Frappe's native Desk permission system untouched. 

If you want Frappe Desk itself (e.g. DocType List view and document access) to be enforced from Topaz ReBAC decisions:

1. Enable the gate in site config:
   ```bash
   bench --site my-site set-config topaz_desk_permissions true
   ```
2. In your app's `topaz_registry` hook, map standard Frappe permission actions (`read`, `write`, etc.) to Topaz relations or permissions. You can map a single relation, an array/list of relations, or a pipe-separated string (evaluated with OR logic):
   ```python
   topaz_registry = {
       "Eduauth Department": {
           "object": {"type": "department", "id_field": "name"},
           "relations": [ ... ],
           "permissions": {
               # Single relation string:
               "read": "is_member",     # List view & read access only if user is member
               "write": "lead",          # Edit access only if user is HOD/lead
           },
       },
       "Eduauth Committee": {
           "object": {"type": "committee", "id_field": "name"},
           "relations": [ ... ],
           "permissions": {
               # Array of relations: access granted if user satisfies ANY of them
               "read": ["is_member", "can_access"],
               "write": "chair | admin",  # Pipe syntax also supported
           },
       },
   }
   ```
   For list views, `topaz_frappe` queries Topaz for resources across all listed relations and unions the allowed ID set into the SQL query predicate.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/topaz_frappe
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

apache-2.0
