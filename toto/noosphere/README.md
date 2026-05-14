# noosphere

Drop-in Django app for selective per-model sync rules with `RemotePlatform`
and deployment-configurable transport classes.

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
  transports.py
  transport_registry.py
  remote.py
  services/
    __init__.py
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

## Transport configuration

Transport backend choices come from `settings.NOOSPHERE_TRANSPORTS`.

Example:

```python
NOOSPHERE_TRANSPORTS = {
    "default": "noosphere.transports.RequestsTransport",
    "tor": "noosphere.transports.TorTransport",
    "tor_browser": "noosphere.transports.TorBrowserTransport",

    # Deployment-specific examples:
    # "studio_uplink": "portal.noosphere_transports.StudioUplinkTransport",
    # "studio_downlink": "portal.noosphere_transports.StudioDownlinkTransport",
}
```

`RemotePlatform.uplink_backend` and `RemotePlatform.downlink_backend`
are limited in admin to these keys.

If the setting is missing, only:

```python
"default": "noosphere.transports.RequestsTransport"
```

is available.

## Tor

Tor transport requires:

```bash
pip install "requests[socks]"
```

`TorTransport` uses `socks5h://127.0.0.1:9050`.
`TorBrowserTransport` uses `socks5h://127.0.0.1:9150`.

## Important

This app expects your existing core services:

```python
toto.core.services.backup_service.BackupService
toto.core.services.sync_service.SyncService
```

The noosphere services subclass/wrap those core services. They do not duplicate ZIP, signature, hash, manifest, FK, or Geo mechanics.

See `PATCH_NOTES.md`.
