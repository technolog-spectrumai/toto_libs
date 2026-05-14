"""
Example for a domain app's sync_adapters.py.

Copy this into something like toto/core/sync_adapters.py
and import it in that app's AppConfig.ready().
"""

from noosphere.adapters import BaseSyncAdapter
from noosphere.registry import register_sync_adapter

from .models import Font, Theme, ColorMix


@register_sync_adapter
class FontSyncAdapter(BaseSyncAdapter):
    model = Font
    allowed_fields = [
        "name",
        "style_family",
        "cdn_link",
    ]


@register_sync_adapter
class ColorMixSyncAdapter(BaseSyncAdapter):
    model = ColorMix
    allowed_fields = [
        "name",
        "primary_bg_light",
        "text_main_light",
        "header_bg_light",
        "bubble_bg_light",
        "appbar_bg_light",
        "appbar_text_light",
        "footer_bg_light",
        "footer_text_light",
        "accent_light",
        "warn_light",
        "success_light",
        "sunken_light",
        "link_light",
        "primary_bg_dark",
        "text_main_dark",
        "header_bg_dark",
        "bubble_bg_dark",
        "appbar_bg_dark",
        "appbar_text_dark",
        "footer_bg_dark",
        "footer_text_dark",
        "accent_dark",
        "warn_dark",
        "success_dark",
        "sunken_dark",
        "link_dark",
        "accent_1",
        "accent_2",
    ]


@register_sync_adapter
class ThemeSyncAdapter(BaseSyncAdapter):
    model = Theme
    allowed_fields = [
        "name",
        "font",
        "color_mix",
    ]
    required_dependencies = [
        "core.Font",
        "core.ColorMix",
    ]
