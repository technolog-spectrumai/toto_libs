from django.core.exceptions import PermissionDenied


def user_vendor_for_shop(user, shop=None):
    if not user.is_authenticated:
        return None
    qs = getattr(user, 'bazaar_vendor_accounts', None)
    if qs is None:
        return None
    qs = qs.filter(status='active')
    if shop:
        qs = qs.filter(shop=shop)
    return qs.first()


def require_vendor(user, shop=None):
    vendor = user_vendor_for_shop(user, shop)
    if not vendor:
        raise PermissionDenied('Vendor access required.')
    return vendor


def is_bazaar_operator(user):
    return user.is_authenticated and (user.is_staff or user.has_perm('bazaar.change_order'))
