from django.core.exceptions import ImproperlyConfigured


class BaseSyncAdapter:
    """
    Base adapter for one syncable model.
    """

    model = None
    model_label = None

    allowed_fields = []
    readonly_fields = []
    required_dependencies = []

    def get_queryset(self, rule):
        qs = self.model.objects.all()
        filters = rule.filters or {}

        for key, value in filters.items():
            if key == "changed_since_last_sync":
                continue

            if key == "only_active":
                if value and self.has_field("is_active"):
                    qs = qs.filter(is_active=True)
                elif value and self.has_field("active"):
                    qs = qs.filter(active=True)
                continue

            if self.has_field(key):
                qs = qs.filter(**{key: value})

        if filters.get("changed_since_last_sync"):
            since = self.get_since(rule)
            if since and self.has_field("updated_at"):
                qs = qs.filter(updated_at__gt=since)

        return qs

    def get_since(self, rule):
        if rule.direction == rule.DIRECTION_UP:
            return rule.last_pushed_at
        if rule.direction == rule.DIRECTION_DOWN:
            return rule.last_pulled_at
        return None

    def get_export_fields(self, rule):
        if rule.fields:
            invalid = set(rule.fields) - set(self.allowed_fields)
            if invalid:
                raise ImproperlyConfigured(
                    f"Fields not allowed for {self.model_label}: {', '.join(sorted(invalid))}"
                )
            return list(rule.fields)

        return list(self.allowed_fields)

    def has_field(self, name):
        try:
            self.model._meta.get_field(name)
            return True
        except Exception:
            return False

    def serialize_preview_object(self, obj):
        return {
            "id": obj.pk,
            "uid": str(obj.uid),
            "name": str(obj),
        }
