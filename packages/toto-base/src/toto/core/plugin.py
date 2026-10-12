# toto/core/plugins.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Type
from django.template.loader import render_to_string


@dataclass(order=True)
class RenderedPlugin:
    order: int
    key: str
    title: str
    html: str
    plugin: "BasePlugin"


@dataclass(order=True)
class RenderedFloatingPlugin:
    order: int
    key: str
    html: str


@dataclass(order=True)
class RenderedHeaderPlugin:
    order: int
    key: str
    html: str


class BasePlugin:
    """
    Generic reusable plugin base.

    Subclasses can own their own registry by defining:

        registry = {}
    """

    registry: ClassVar[dict[str, "BasePlugin"]] = {}

    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    template_name: ClassVar[str] = ""
    order: ClassVar[int] = 100
    enabled: ClassVar[bool] = True

    @classmethod
    def get_key(cls) -> str:
        return cls.key or cls.__name__

    @classmethod
    def get_title(cls) -> str:
        return cls.title or cls.__name__.replace("Plugin", "")

    @classmethod
    def get_order(cls) -> int:
        return cls.order

    @classmethod
    def should_register(cls) -> bool:
        """
        Useful if you want abstract base classes to avoid registering.
        """
        return True

    @classmethod
    def register(cls, plugin_class: Type["BasePlugin"]):
        """
        Decorator-friendly class-level register method.

        Usage:

            @ProfilePlugin.register
            class ExperiencesProfilePlugin(ProfilePlugin):
                ...
        """
        if not issubclass(plugin_class, cls):
            raise TypeError(
                f"{plugin_class.__name__} must inherit from {cls.__name__}"
            )

        if not plugin_class.should_register():
            return plugin_class

        key = plugin_class.get_key()

        if key in cls.registry:
            raise ValueError(f"Plugin already registered: {key}")

        cls.registry[key] = plugin_class()
        return plugin_class

    @classmethod
    def plugin(cls, **options):
        """
        Decorator with config overrides.

        Usage:

            @ProfilePlugin.plugin(
                key="experiences",
                title="Experiences",
                order=20,
            )
            class ExperiencesProfilePlugin(ProfilePlugin):
                ...
        """

        def wrapper(plugin_class: Type["BasePlugin"]):
            for name, value in options.items():
                setattr(plugin_class, name, value)

            return cls.register(plugin_class)

        return wrapper

    @classmethod
    def unregister(cls, key: str):
        cls.registry.pop(key, None)

    @classmethod
    def get(cls, key: str) -> "BasePlugin | None":
        return cls.registry.get(key)

    @classmethod
    def all(cls) -> list["BasePlugin"]:
        return sorted(
            cls.registry.values(),
            key=lambda plugin: plugin.get_order(),
        )

    @classmethod
    def visible(cls, **kwargs) -> list["BasePlugin"]:
        return [
            plugin
            for plugin in cls.all()
            if plugin.is_visible(**kwargs)
        ]

    @classmethod
    def render_all(cls, **kwargs) -> list[RenderedPlugin]:
        rendered = []

        for plugin in cls.all():
            result = plugin.render(**kwargs)
            if result is not None:
                rendered.append(result)

        return sorted(rendered)

    @staticmethod
    def visible_for_request(request) -> bool:
        return True

    def is_enabled(self) -> bool:
        return self.enabled

    def is_visible(self, **kwargs) -> bool:
        request = kwargs.get("request")

        if not self.is_enabled():
            return False

        if request is not None and not self.visible_for_request(request):
            return False

        return True

    def get_context(self, **kwargs) -> dict[str, Any]:
        return {}

    def build_context(self, **kwargs) -> dict[str, Any]:
        context = {}

        base_context = kwargs.get("base_context")
        if base_context:
            context.update(base_context)

        context.update(kwargs)
        context.update(self.get_context(**kwargs))

        return context

    def render_html(self, **kwargs) -> str:
        request = kwargs.get("request")

        return render_to_string(
            self.template_name,
            context=self.build_context(**kwargs),
            request=request,
        )

    def render(self, **kwargs) -> RenderedPlugin | None:
        if not self.is_visible(**kwargs):
            return None

        return RenderedPlugin(
            order=self.get_order(),
            key=self.get_key(),
            title=self.get_title(),
            html=self.render_html(**kwargs),
            plugin=self,
        )


class FloatingPlugin(BasePlugin):
    """
    Base for globally-injected floating UI widgets (e.g. chat buttons, help overlays).

    Rendered into a fixed-position container in oya/base.html via the
    ``render_floating_plugins`` template tag.
    """

    registry: ClassVar[dict[str, "FloatingPlugin"]] = {}

    def render(self, **kwargs) -> RenderedFloatingPlugin | None:
        if not self.is_visible(**kwargs):
            return None
        return RenderedFloatingPlugin(
            order=self.get_order(),
            key=self.get_key(),
            html=self.render_html(**kwargs),
        )

    @classmethod
    def render_all(cls, **kwargs) -> list[RenderedFloatingPlugin]:
        rendered = []
        for plugin in cls.all():
            result = plugin.render(**kwargs)
            if result is not None:
                rendered.append(result)
        return sorted(rendered)


class HeaderPlugin(BasePlugin):
    """Widgets in the app bar itself — a balance, a health bar, a presence dot.

    The header had no slot for anything per-user: `HEADER_NAV_ITEMS` takes
    static links, and `FloatingPlugin` renders into a fixed corner. A widget
    that must be seen on every page at every width (the mana chip) needs to
    sit beside the brand, so this registry mirrors `FloatingPlugin` and
    `{% render_header_plugins %}` renders it there, once for the bar and once
    (``variant="mobile"``) inside the menu the bar folds into.

    **What stays when the bar folds** (2026-10-12; the owner: "we need all
    app bar icons to be able to fold in mobile version into hamburger menu
    dropdown. what will stay will be: logo, name, mana, and hamburger on the
    right"). Below the bar's breakpoint (`oya/header.html`: Tailwind's `lg`)
    the bar keeps the brand, the widgets that say ``pinned = True`` and the
    menu button; every other widget is folded by the HEADER, which wraps the
    unpinned ones in a group it hides, so a widget writes no breakpoint of
    its own and a new one folds without being told to. The mana chips are
    pinned. In the menu every widget renders its ``"mobile"`` variant, a
    pinned one too (the chip's fuller row).

    Registered by explicit import from an app's ``ready()``, exactly like the
    gas pump (`toto.quota.apps`) — nothing here is auto-discovered.
    """

    registry: ClassVar[dict[str, "HeaderPlugin"]] = {}

    #: Stays on the bar at every width. False: folded into the menu below
    #: the bar's breakpoint, as every link of the bar is.
    pinned: ClassVar[bool] = False

    def render(self, **kwargs) -> RenderedHeaderPlugin | None:
        if not self.is_visible(**kwargs):
            return None
        return RenderedHeaderPlugin(
            order=self.get_order(),
            key=self.get_key(),
            html=self.render_html(**kwargs),
        )

    @classmethod
    def render_all(cls, pinned: bool | None = None, **kwargs) -> list[RenderedHeaderPlugin]:
        """Every visible widget, in order. ``pinned`` narrows it to the
        widgets that stay on the bar (True) or to the ones that fold (False);
        None is all of them."""
        rendered = []
        for plugin in cls.all():
            if pinned is not None and bool(getattr(plugin, "pinned", False)) != bool(pinned):
                continue
            result = plugin.render(**kwargs)
            if result is not None:
                rendered.append(result)
        return sorted(rendered)
