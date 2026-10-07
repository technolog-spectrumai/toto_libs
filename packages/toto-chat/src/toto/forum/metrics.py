"""What the forum meters (2026-10-07, stage 69). Pure data: no models, no
database, no settings; imported by ``QuotaConfig.ready()``.

Two things a post keeps, charged storage mana once, when the post is stored:

* ``forum.text_kb`` — a kilobyte (1024 bytes) of the message's text, counted
  in UTF-8 bytes;
* ``forum.image_kb`` — a kilobyte of the message's image, counted before it
  is sealed. The dearer of the two.

A post of 300 bytes is 300/1024 of a kilobyte, exactly: the quantity is a
fraction, never rounded (``billing.kilobytes``). Reading, removing a message,
polls and votes are free. The prices are rows of the platform's rate card
(``toto/mana/colours.py`` seeds them; ``billing.py`` says how a post is
charged).
"""

from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import Metric, registry

APP = "forum"
TEXT_KB = "forum.text_kb"
IMAGE_KB = "forum.image_kb"
#: The unit of both: the rate card's price is per one of these.
UNIT = "kb"

#: The caps are a day's ceiling, not a price: about 250 messages of the
#: greatest length, and ten pictures of the greatest size.
registry.register(Metric(
    code=TEXT_KB, label=_("Forum text"), app_label=APP, unit=UNIT,
    description=_("Kilobytes of message text posted in the forum."),
    default_limit=2048,
))
registry.register(Metric(
    code=IMAGE_KB, label=_("Forum image"), app_label=APP, unit=UNIT,
    description=_("Kilobytes of images posted in the forum."),
    default_limit=102400,
))

#: Both, in the order a post is charged.
ALL = (TEXT_KB, IMAGE_KB)
