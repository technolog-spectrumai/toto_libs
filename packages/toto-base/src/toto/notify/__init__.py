"""Notifications: what happened to a member's files, shares, jobs and account,
kept for them and shown in the app bar's bell (2026-10-04).

    from toto import notify

    notify.send(user, "job.transfer_done", bucket="Work", bucket_id=7,
                link="/vault/transfers/12/")

``send`` writes one ``Notification`` row and, once the transaction commits,
pokes the member's open pages through the live socket
(``toto.core.live``, group ``user.<pk>``). With no channel layer it still
writes the row, so nothing depends on a socket to be correct: the bell asks
for its list over plain HTTP every minute while no socket is open.

What a row keeps is the KIND and its text parameters, never a rendered
sentence, so the bell says it in the reader's language when it is drawn
(``kinds.py``). Read rows are pruned thirty days after they were read
(``services.prune``, the nightly beat).

Optional: a host that does not install ``toto.notify`` gets no bell, no
table and no socket, and every caller in the library asks
``apps.is_installed("toto.notify")`` first.
"""


def send(*args, **kwargs):
    """``services.send`` — imported late, so naming this package costs no
    model import before the app registry is ready."""
    from .services import send as _send

    return _send(*args, **kwargs)
