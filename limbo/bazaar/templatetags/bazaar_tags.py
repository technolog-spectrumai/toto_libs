from django import template
from toto.bazaar.permissions import user_vendor_for_shop

register = template.Library()


@register.simple_tag(takes_context=True)
def get_user_vendor(context):
    request = context.get("request")
    if not request:
        return None
    return user_vendor_for_shop(request.user)


@register.simple_tag(takes_context=True)
def get_has_wallet_pin(context):
    request = context.get("request")
    if not request or not request.user.is_authenticated:
        return False
    from toto.bazaar.wallet_pin import has_wallet_pin
    return has_wallet_pin(request.user)
