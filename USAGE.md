# topaz_frappe — Usage Reference

`topaz_frappe` is the **control plane** for a Topaz directory, built on Frappe.
You author objects and relationships as Frappe documents; this library writes
them to Topaz. Topaz then serves authorization decisions (AuthZen) to your
downstream services and stores the relationship graph.

> **Mental model:** Frappe = authoring UI + data store for *core* objects you
> choose to manage here. Topaz = the source of truth for authorization. This
> library does **not** replace Topaz and does not define domain objects of its
> own — each consuming app declares its own mappings.

---

## Contents

1. [Configuration](#1-configuration)
2. [Architecture](#2-architecture)
3. [Declaring a mapping (`topaz_registry`)](#3-declaring-a-mapping)
4. [The `managed` flag](#4-the-managed-flag)
5. [Opt-in Desk permissions](#5-opt-in-desk-permissions)
6. [How sync works](#6-how-sync-works)
7. [CLI commands](#7-cli-commands)
8. [Mock transport & testing](#8-mock-transport--testing)
9. [Deferred: service-to-service API](#9-deferred-service-to-service-api)

---

## 1. Configuration

All settings live in the site's `site_config.json` (set via
`bench --site <site> set-config <key> <value>`). Everything is optional; the
library degrades to a no-op when Topaz is disabled.

| Key | Default | Meaning |
| --- | --- | --- |
| `topaz_enabled` | `false` | Master switch for **sync**. When off, all sync hooks are no-ops. |
| `topaz_host` | `http://localhost:9292` | Topaz reader/writer REST base URL. |
| `topaz_api_key` | – | Optional bearer token sent to Topaz. |
| `topaz_sync_queue` | `short` | RQ queue for background sync jobs. |
| `topaz_subject_type` | `user` | Topaz object type used for Frappe users. |
| `topaz_perm_cache_enabled` | `true` | Per-request memoisation of permission checks. |
| `topaz_inflight_threshold` | `5000` | Chunk size for `IN (...)` predicates in list filters. |
| `topaz_desk_permissions` | `false` | **Opt-in.** Enforce Frappe Desk permissions from Topaz (see §5). |

Example:

```bash
bench --site my-site set-config topaz_enabled true
bench --site my-site set-config topaz_host http://topaz:9292
bench --site my-site set-config topaz_sync_queue long
```

Read the effective settings at runtime:

```python
from topaz_frappe.config.settings import get_topaz_settings
get_topaz_settings().as_dict()
```

---

## 2. Architecture

```
Consumers
├─ Frappe Desk user (human authoring of documents)
├─ Downstream microservices (Java/Go/FastAPI) ──► talk to Topaz directly
│                                                (AuthZen / Topaz APIs)
└─ (optional) Frappe app wanting its Desk perms answered from Topaz
         │
         ▼
topaz_frappe
├─ core/registry.py     per-app DocType → mapping (topaz_registry hook)
├─ core/hooks_bridge.py doc_events → after_commit → dedup background job
├─ sync/engine.py       minimal-delta object/relation writer + Redis snapshot
├─ sync/diff.py         pure add/delete delta
├─ transport/           HttpTransport (REST v3) | MockTransport | factory
├─ permissions/         OPT-IN has_permission + list filter (see §5)
└─ commands/            bench topaz-sync / topaz-health / topaz-prune
```

Module boundaries:

| Module | Responsibility | Frappe coupling |
| --- | --- | --- |
| `core/domain.py` | Immutable value objects & mapping specs | none (pure) |
| `core/registry.py` | DocType → mapping table | `frappe.get_hooks` |
| `core/sync_mixin.py` | Resolve object/relations from a doc | frappe |
| `core/hooks_bridge.py` | Lifecycle → commit-safe jobs | frappe |
| `sync/` | Diff & apply | frappe (cache, log) |
| `transport/` | Topaz I/O | none (pure `requests`) |
| `permissions/` | Opt-in Desk authz | frappe |

**Worker safety.** Per-request state (`_topaz_perm_cache`, pending jobs, test
overrides) lives on `frappe.local` / `frappe.flags` (thread/request-local). The
HTTP transport is cached **per worker thread**, keyed by host, so connection
pooling is reused across the requests a gunicorn thread serves without
cross-request leakage. The registry load is lock-guarded and idempotent.

---

## 3. Declaring a mapping

Mappings are declared by **consuming apps**, not by `topaz_frappe`. Add a
`topaz_registry` dict to your app's `hooks.py`. Frappe merges these across all
installed apps.

### Shape

```python
topaz_registry = {
    "<DocType>": {
        "object": {"type": "committee", "id_field": "name"},
        "relations": [ ...relation specs... ],
        # Optional: per-DocType Desk permission relation overrides (see §5)
        "permissions": {"read": "can_access_committee"},
        # Optional: mark as not managed by Frappe (see §4)
        "managed": True,
    },
}
```

### Object spec

| Key | Meaning |
| --- | --- |
| `type` | Topaz object type (namespace), e.g. `committee`. |
| `id_field` | Frappe field used as the object id. Default `name`. |
| `id_transform` | Optional callable normalising the raw id (e.g. `str.lower`). |

### Relation specs

Each spec describes how one or more topes are derived from the document.

**`link`** — a Link field on the document becomes one tuple.

```python
{"kind": "link", "relation": "owner",
 "target_type": "user", "field": "owner"}
```

| Key | Meaning |
| --- | --- |
| `relation` | Relation name to write. |
| `target_type` | Subject object type. |
| `field` / `fieldname` | Link field on the document. |
| `subject_relation` | Optional subject-set relation (`group:eng#member`). |
| `transform` | Optional callable normalising the field value. |

**`child`** — each row of a child table becomes one tuple.

```python
{"kind": "child", "table": "members",
 "subject_field": "user", "static_relation": "member"}
```

| Key | Meaning |
| --- | --- |
| `table` | Child table field name. |
| `subject_field` | Row field holding the subject id. |
| `static_relation` | Fixed relation for every row. |
| `relation_field` | Or: read the relation name from this row field. |
| `subject_type_field` | Or: read the subject type from this row field. |
| `default_subject_type` | Fallback subject type. Default `user`. |
| `subject_relation` / `subject_relation_field` | Optional subject-set relation. |
| `condition` | `callable(row) -> bool` to skip rows. |

> A `child` spec needs **either** `static_relation` **or** `relation_field`.

**`computed`** — full programmatic control.

```python
# generator is a callable (doc) -> Iterable[TopazRelation] | TopazRelation | None
{"kind": "computed", "generator": "my_app.mappings.committee_relations"}
```

The `kind` may be omitted — it is inferred: `generator` → computed,
`table` → child, otherwise → link.

### Programmatic API

The same structures can be built with dataclasses
(`topaz_frappe.core.domain`) and registered directly:

```python
from topaz_frappe.core.domain import (
    DocTypeMapping, TopazObjectSpec, LinkRelation, ChildTableRelation,
)
from topaz_frappe.core.registry import registry

registry.register("Committee", DocTypeMapping(
    object_spec=TopazObjectSpec(object_type="committee", id_field="name"),
    relations=[
        LinkRelation(relation="owner", target_type="user", fieldname="owner"),
        ChildTableRelation(table="members", subject_field="user",
                           static_relation="member"),
    ],
    permission_relations={"read": "can_access_committee"},
))
```

---

## 4. The `managed` flag

`managed` (default `True`) marks whether **Frappe is authoritative** for the
object type.

- `managed: True` — the sync engine writes/prunes this type. `topaz-sync` and
  `topaz-prune` include it; document events sync it.
- `managed: False` — Frappe does **not** own the type. Use this when an
  external service writes that object type to Topaz directly, but you still
  want to declare a mapping here for the opt-in Desk-permission path (§5).
  Such types are **never** synced or pruned from Frappe.

`registry.managed_doctypes()` returns only the managed set. `topaz-prune`
iterates exactly that set, so object types owned by external services are
provably untouched.

---

## 5. Opt-in Desk permissions

> **Default: OFF.** `topaz_frappe`'s primary job is authoring + sync. Enforcing
> Frappe Desk permissions from Topaz is an *optional* mode for Frappe apps that
> themselves want to consume Topaz for access control.

Enable per site:

```bash
bench --site my-site set-config topaz_desk_permissions true
```

Both `topaz_enabled` and `topaz_desk_permissions` must be true
(`is_desk_permissions_enabled()`). When off, the `has_permission` and
`permission_query_conditions` wildcard handlers are strict no-ops and Frappe's
native role/permission system is untouched.

### What it does when on

- **`has_permission`** maps a Frappe `ptype` to a Topaz relation and calls
  Topaz `Check`. Default map (fallback): `read → viewer`, `write/create/submit/
  amend → editor`, `delete/cancel/share → admin`, `select/report/export/print/
  email → viewer`. Administrator and `ignore_permissions` bypass; unregistered
  DocTypes defer to Frappe; transport failures fail **closed**.
- **`permission_query_conditions`** asks Topaz for the set of object ids the
  subject can see (reverse lookup / `search`) and emits a local, chunked
  `` `tab{X}`.`name` IN (...) `` predicate. Wildcard (`*`) → no filter;
  empty set → `1 = 0`.

### Per-DocType relation overrides

Override the default `ptype → relation` on a per-DocType basis via the mapping's
`permissions` key. This is how you wire relation names your schema defines
(e.g. `can_access_committee`) without touching this library:

```python
topaz_registry = {
    "Committee": {
        "object": {"type": "committee"},
        "relations": [ ... ],
        "permissions": {
            "read":  "can_access_committee",
            "write": "chair",
        },
    },
}
```

Notes:

- The value is a single relation name; implication between relations
  (`admin ⇒ editor ⇒ viewer`) is the **Topaz schema's** responsibility, not
  Frappe's. Define composed relations in your schema if you need layering.
- Use `"*"` as a ptype to map all otherwise-unmapped permission types for that
  DocType. Lookup order: explicit `ptype` → `"*"` → global default.
- The list filter uses whatever `read` resolves to for that DocType.

---

## 6. How sync works

For every `after_insert` / `on_update` / `on_trash` on a **registered,
managed** DocType:

1. The doc is resolved to a `TopazObject` and a set of `TopazRelation`s.
2. The work is deferred to a background job that runs **after the surrounding
   transaction commits** (`frappe.db.after_commit`), so rolled-back writes
   never reach Topaz.
3. Jobs are de-duplicated per `(doctype, name, method)` within a transaction.
4. On execution the engine computes the **minimal delta** against the last
   applied set (cached in Redis under `topaz_relations::<type>::<id>` for 30
   days), then `put_object` + only the needed `set_relation` / `delete_relation`
   calls.
5. A record is written to the **Topaz Sync Log** DocType.

In tests / when `frappe.flags.topaz_sync_immediate` is set, the sync runs
synchronously so assertions observe results immediately.

Deletes are cascading: `sync_delete` removes the Topaz object (Topaz drops its
relationships).

---

## 7. CLI commands

All commands run inside a site context (`bench --site <site> ...`) and require
`topaz_enabled`.

```bash
# Bulk (re)sync every managed registered DocType
bench --site my-site topaz-sync

# Sync a single DocType
bench --site my-site topaz-sync --doctype Committee

# Connectivity / latency / sample authorization probe
bench --site my-site topaz-health
bench --site my-site topaz-health --object-type committee --object-id ENG-CMT --relation viewer

# Reconcile Topaz against Frappe; prune objects missing in Frappe
bench --site my-site topaz-prune --dry-run
bench --site my-site topaz-prune
```

`topaz-sync` and `topaz-prune` operate **only** on `managed` types.

---

## 8. Mock transport & testing

`MockTransport` is a full in-memory directory implementing the transport
protocol, including **subject-set / nested-group semantics** (see
`topaz_frappe/transport/mock.py`). Inject it for a request/site:

```python
import frappe
from topaz_frappe.transport.mock import MockTransport

frappe.flags.topaz_transport = MockTransport()   # per-request override
```

Run the suite:

```bash
bench --site my-site run-tests --app topaz_frappe
```

The tests cover diff/domain (pure), mapping specs (pure), the mock transport
(including nested groups and cycle safety), sync engine integration, and the
opt-in permission gate (on and off).

---

## 8a. Worked example

See [`docs/HOWTO-education-institute.md`](./docs/HOWTO-education-institute.md)
for an end-to-end how-to modelling an education institute (institution,
department, committee, project, groups, roles) from Frappe documents into the
Topaz core model.

---

## 9. Deferred: service-to-service API

A dedicated s2s API (`/api/method/topaz_frappe.api.*` for raw object/relationship
management by non-Frappe services) is **intentionally not implemented yet**.
Today, services manage **core** objects either through the standard Frappe REST
API for the managed DocTypes, or by talking to Topaz directly for object types
they own. See the roadmap discussion before adding the s2s surface.
