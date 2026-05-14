# Noosphere

Minimal Django app for per-model sync policy and sync audit logging.

This package contains only:

- `models.py`
- `admin.py`
- `apps.py`
- empty migrations package

It intentionally does **not** include views, URLs, API endpoints, or services.

## Install

Add to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    "noosphere",
]
```

Then run:

```bash
python manage.py makemigrations noosphere
python manage.py migrate
```

## Assumptions

The models reference your existing platform model as:

```python
"core.Platform"
```

If your app label is different, change the FK strings in `models.py`:

```python
platform = models.ForeignKey("core.Platform", ...)
```

## Models

- `SyncRule` — one model, one direction, selected fields/filters/policy.
- `SyncRun` — one execution of a sync rule.
- `SyncObjectRun` — optional per-object audit log.

No `RemoteObjectCache` is included. Remote previews should be loaded live through admin/API later.
