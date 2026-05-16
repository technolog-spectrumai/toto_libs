from django.utils.text import slugify


def unique_slug(instance, value, *, fallback="item"):
    base_slug = slugify(value) or fallback
    slug = base_slug
    counter = 1
    queryset = instance.__class__.objects.all()
    if instance.pk:
        queryset = queryset.exclude(pk=instance.pk)
    while queryset.filter(slug=slug).exists():
        counter += 1
        slug = f"{base_slug}-{counter}"
    return slug
