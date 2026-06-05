from django.contrib import admin

from .models import (
    ImageTransform,
    ImageTransformParam,
    OcrImage,
    OcrLine,
    OcrProject,
)

admin.site.register(OcrProject)
admin.site.register(OcrImage)
admin.site.register(OcrLine)
admin.site.register(ImageTransform)
admin.site.register(ImageTransformParam)
