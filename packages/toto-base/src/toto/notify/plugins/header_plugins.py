"""The bell in the app bar (2026-10-04): an icon and the unread count, no
words — on the desktop bar, and in the phone menu (``variant="mobile"``).

The markup is ``notify/plugins/_bell.html``; what it does is
``notify/live.js``, which reads everything it needs — the three doors'
addresses, the socket's — from the element's ``data-`` attributes. Nothing
the server knows is written into a script.
"""

from toto.core.plugin import HeaderPlugin


@HeaderPlugin.plugin(key="notify_bell", order=20)
class BellPlugin(HeaderPlugin):
    template_name = "notify/plugins/_bell.html"

    def visible_for_request(self, request):
        user = getattr(request, "user", None)
        return bool(user is not None and getattr(user, "is_authenticated", False))

    def get_context(self, **kwargs):
        from django.urls import NoReverseMatch, reverse

        from .. import services
        from ..ws import socket_path

        context = super().get_context(**kwargs)
        request = kwargs.get("request")
        # One count per page, shared by the bar's bell and the phone menu's.
        unread = getattr(request, "_toto_notify_unread", None)
        if unread is None:
            unread = services.unread_count(request.user)
            request._toto_notify_unread = unread
        try:
            urls = {"list": reverse("notify:api_list"), "read": reverse("notify:api_read"),
                    "read_all": reverse("notify:api_read_all")}
        except NoReverseMatch:      # installed, but the host mounts no doors
            urls = None
        context.update({
            "notify_unread": unread,
            "notify_urls": urls,
            "notify_ws_path": socket_path(),
            "variant": kwargs.get("variant", "bar"),
        })
        return context

    def render_html(self, **kwargs):
        context = self.get_context(**kwargs)
        if context["notify_urls"] is None:
            return ""               # no doors: no bell, not a dead one
        from django.template.loader import render_to_string

        return render_to_string(self.template_name, context, request=kwargs.get("request"))
