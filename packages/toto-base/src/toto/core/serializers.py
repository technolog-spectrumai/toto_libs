from rest_framework import serializers
from toto.core import palette
from toto.core.models import Platform, Font


class FontSerializer(serializers.ModelSerializer):
    class Meta:
        model = Font
        fields = [
            "id",
            "name",
            "cdn_link",
            "style_family"
        ]


class PlatformSerializer(serializers.ModelSerializer):
    #: The shape pages were always handed, when it was a Theme row:
    #: ``{name, theme: {colors}, header: {light, dark}, font}``. The colours
    #: and the header's classes are the one palette's (toto/core/palette.py);
    #: only the font is this platform's own.
    theme = serializers.SerializerMethodField()

    class Meta:
        model = Platform
        fields = [
            "id",
            "domain",
            "site_name",
            "publication_year",
            "active",
            "theme",
            "author"
        ]

    def get_theme(self, platform):
        font = FontSerializer(platform.font).data if platform.font_id else None
        return {**palette.theme(), "font": font}
