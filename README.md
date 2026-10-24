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
   bench --site my-site set-config topaz_host http://localhost:9292
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
