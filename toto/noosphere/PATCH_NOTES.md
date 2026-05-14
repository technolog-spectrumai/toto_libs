# Required core patches

These are small backwards-compatible changes to your existing core backup services.

## BackupEngine.serialize_model

Change:

```python
def serialize_model(self, model):
```

to:

```python
def serialize_model(self, model, queryset=None, fields=None):
```

Use `queryset` when provided, otherwise `model.objects.all()`.

## BackupEngine.serialize_object

Change:

```python
def serialize_object(self, obj):
```

to:

```python
def serialize_object(self, obj, fields=None):
```

Then skip fields not in `fields` when `fields` is non-empty.

## BackupService.create_backup

Change:

```python
def create_backup(self, output_path=None):
```

to:

```python
def create_backup(
    self,
    output_path=None,
    model_labels=None,
    queryset_map=None,
    field_map=None,
    sync_meta=None,
):
```

Then:

- add `manifest["sync"] = sync_meta` when provided
- skip models not in `model_labels` when provided
- call `serialize_model(model, queryset=..., fields=...)`

All new arguments are optional, so your existing backup console keeps working.

## Optional SyncService._resolve_fields patch

If your backup engine serializes raw FK `attname` values like `owner_id`, make `_resolve_fields()` tolerate field names that are not returned by `model._meta.get_field()` and assign them directly into `defaults`.
