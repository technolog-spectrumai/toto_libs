"""Notifications: what somebody else did to the files in a bucket a member
owns, kept for them and shown in the app bar's bell (2026-10-04).

    from toto import notify

    notify.send(owner, "vault.uploaded", actor=ada, collapse="uploaded:7",
                title="plan.pdf", bucket="Work", bucket_id=7,
                link="/vault/public/?bucket=work")

``send`` writes one ``Notification`` row and nothing else: nobody is woken
and nothing is published. The member's bell reads the row the next time it
asks its door — when a page loads, or when its tab is looked at again
(``notify/bell.js``); no request is held open for it (2026-10-06).

What a row keeps is the KIND and its text parameters, never a rendered
sentence, so the bell says it in the reader's language when it is drawn
(``kinds.py``). Read rows are pruned thirty days after they were read
(``services.prune``, the nightly beat).

Optional: a host that does not install ``toto.notify`` gets no bell and no
table, and every caller in the library asks
``apps.is_installed("toto.notify")`` first.
"""


def send(*args, **kwargs):
    """``services.send`` — imported late, so naming this package costs no
    model import before the app registry is ready."""
    from .services import send as _send

    return _send(*args, **kwargs)
