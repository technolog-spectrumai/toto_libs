from rest_framework import serializers
from oya.models import Theme, Platform, Font


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
    header_classes_light = serializers.SerializerMethodField()
    header_classes_dark = serializers.SerializerMethodField()
    font = FontSerializer()
    class Meta:
        model = Theme
        fields = [
            "id",
            "name",
            "theme",
            "font",
            "header_classes_light",
            "header_classes_dark"
        ]

    def get_header_classes_light(self, obj):
        return obj.get_header_classes(dark_mode=False)

    def get_header_classes_dark(self, obj):
        return obj.get_header_classes(dark_mode=True)


class PlatformSerializer(serializers.ModelSerializer):
    theme = ThemeSerializer()

    class Meta:
        model = Platform
        fields = [
            "id",
            "domain",
            "site_name",
            "company",
            "publication_year",
            "active",
            "tls_certificate",
            "theme"
        ]
