# noosphere

Drop-in Django app for selective per-model sync rules with `RemotePlatform`.

This package includes:

```text
noosphere/
  __init__.py
  apps.py
  models.py
  admin.py
  forms.py
  adapters.py
  registry.py
  autodiscover.py
  remote.py
  services/
    __init__.py
    client.py
    package_builder.py
    importer.py
    runner.py
  migrations/
    __init__.py

templates/admin/noosphere/
  remote_platform_sync_console.html
  remote_preview.html
```

It intentionally does **not** include API views or URL routes.

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

## Models

- `RemotePlatform`: a remote Django project/server with URL, auth, SSL, timeout.
- `SyncRule`: selected model + direction + remote platform + field/filter policy.
- `SyncRun`: one execution log.
- `SyncObjectRun`: per-object audit log.

## Admin

`RemotePlatformAdmin` includes an admin sync console where a user can choose registered sync models and create/update `SyncRule` rows.

## Important

This app expects your existing core services:

```python
toto.core.services.backup_service.BackupService
toto.core.services.sync_service.SyncService
```

The noosphere services subclass/wrap those core services. They do not duplicate ZIP, signature, hash, manifest, FK, or Geo mechanics.

## Required core patch

Your core `BackupService.create_backup()` must accept:

```python
model_labels=None
queryset_map=None
field_map=None
sync_meta=None
```

Your core `BackupEngine.serialize_model()` should accept:

```python
queryset=None
fields=None
```

Your core `BackupEngine.serialize_object()` should accept:

```python
fields=None
```

See `PATCH_NOTES.md`.
