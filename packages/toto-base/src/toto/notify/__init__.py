"""Notifications: what somebody else did to the files in a bucket a member
owns, kept for them and shown in the app bar's bell (2026-10-04).

    from toto import notify

    notify.send(owner, "vault.uploaded", actor=ada, collapse="uploaded:7",
                title="plan.pdf", bucket="Work", bucket_id=7,
                link="/vault/public/?bucket=work")

``send`` writes one ``Notification`` row and, once the transaction commits,
publishes the member's key (``toto.core.live``, ``user.<pk>``), which wakes
the long polls their open pages hold at ``notify:api_wait``. Nothing depends
on that signal to be correct: the row is in the database, and the door reads
it at every poll.

What a row keeps is the KIND and its text parameters, never a rendered
sentence, so the bell says it in the reader's language when it is drawn
(``kinds.py``). Read rows are pruned thirty days after they were read
(``services.prune``, the nightly beat).

Optional: a host that does not install ``toto.notify`` gets no bell, no
table and no long-poll door, and every caller in the library asks
``apps.is_installed("toto.notify")`` first.
"""


def send(*args, **kwargs):
    """``services.send`` — imported late, so naming this package costs no
    model import before the app registry is ready."""
    from .services import send as _send

    return _send(*args, **kwargs)
