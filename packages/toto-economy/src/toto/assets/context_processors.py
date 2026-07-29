from django.apps import apps


def economy_apps(request):
    """Tell templates which economy apps this build actually carries.

    The ledger is usable on its own, so ``assets/base.html`` must not reverse a
    bourse URL that does not exist. Asking the app registry rather than reading
    BUILD_BOURSE keeps the answer true no matter how the app got installed.

    toto.core.context_processors.build_flags does the same job for the flags it
    knows about, but it ships in a pinned wheel and cannot grow a key for an app
    this host owns.
    """
    return {
        "HAS_BOURSE": apps.is_installed("toto.bourse"),
        "HAS_TARIFFS": apps.is_installed("toto.tariffs"),
    }
