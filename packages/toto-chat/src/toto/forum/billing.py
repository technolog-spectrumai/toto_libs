"""The seam where a post is charged.

``posting.post_message`` calls ``settle_post`` INSIDE the transaction that
stores the message, after the message's row and its image are written:

    with transaction.atomic():
        ... the message row, the sealed image in the vault ...
        billing.settle_post(user, message, text_bytes=…, image_bytes=…)

So whatever is put here is atomic with the message: an exception raised from
it rolls the message back (a failed post is never charged, a refused charge
never leaves a message), and a retry of the same press never arrives here at
all — the posting door answers a known ``op`` with the message already
stored (``posting.post_message``).

In this stage it charges nothing: the price per KB of text and per KB of
image, the check for funds before the transaction and the estimate shown
before posting are the next stage's, and this function is where they land.
"""

from __future__ import annotations


def settle_post(user, message, *, text_bytes: int, image_bytes: int) -> None:
    """Charge ``user`` for one stored ``message``: ``text_bytes`` of UTF-8
    text and ``image_bytes`` of image (0 without one). Nothing yet."""
    return None
