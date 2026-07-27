from rest_framework import serializers
from toto.core.models import Platform, Font, Theme


class FontSerializer(serializers.ModelSerializer):
    class Meta:
        model = Font
        fields = [
            "id",
            "name",
            "cdn_link",
            "style_family"
        ]


class ThemeSerializer(serializers.ModelSerializer):
    font = FontSerializer()
    class Meta:
        model = Theme
        fields = [
            "id",
            "name",
            "theme",
            "font",
            "header"
        ]


class PlatformSerializer(serializers.ModelSerializer):
    theme = ThemeSerializer()
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