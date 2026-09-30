"""Sample decks, so the Presentations page opens on something.

Built through ``presentation_format`` rather than by writing ``.pxml`` by hand:
that module decides what a valid deck is, and a sample assembled from raw markup
would be the first file to break the next time the format moves — in the one
place where breakage looks like "the viewer is broken".

Behind ``--full``.

The samples live in the shared ``samples`` bucket beside the sheets and
documents, addressed through ``toto.samples`` where the host provides it. That
module belongs to zenobia, not to this wheel, so its absence is an ordinary
"nothing to do" rather than an error: a host that ships memo without it simply
gets no samples.
"""

from __future__ import annotations

from toto.ingress import IngressCommand

FOLDER = "Presentations"


#: The samples are LIGHT, though `presentation_format.DEFAULT_THEME` is
#: "black". A sample deck is the first thing anybody opens and the thing they
#: copy to start their own, so it should look like the paper most decks end up
#: being — and a dark deck exported to PDF prints a full-bleed black page,
#: which is a poor first impression and a lot of toner. Somebody who wants the
#: dark theme changes one dropdown; the default was the wrong way round for a
#: sample.
SAMPLE_THEME = "white"


def _deck(fmt, title, slides):
    return fmt.Presentation(title=title, slides=slides, theme=SAMPLE_THEME)


def _slide(fmt, title, layout, blocks):
    return fmt.Slide(id=fmt._new_id("s"), title=title, layout=layout,
                     blocks=blocks)


def _text(fmt, payload, slot=""):
    return fmt.Block(id=fmt._new_id("b"), type="text", payload=payload,
                     slot=slot)


def _list(fmt, items, slot=""):
    return fmt.Block(id=fmt._new_id("b"), type="list", items=items, slot=slot)


def _heading(fmt, payload, slot=""):
    return fmt.Block(id=fmt._new_id("b"), type="heading", payload=payload,
                     slot=slot)


def build(fmt) -> list:
    """The sample decks, as ``[(filename, Presentation)]``.

    Two of them, using different layouts on purpose: a sample set where every
    slide is title-and-content demonstrates one layout and implies there are no
    others.
    """
    welcome = _deck(fmt, "Welcome to the platform", [
        _slide(fmt, "Welcome", "section", [
            _text(fmt, "A short tour of what this deck viewer does."),
        ]),
        _slide(fmt, "What a deck is here", "title-content", [
            _list(fmt, [
                "A file in the vault, like any other",
                "Read and presented, never edited in the browser",
                "Exported to PDF by a runner in your Compute Gear",
            ]),
        ]),
        _slide(fmt, "Two columns", "two-column", [
            _heading(fmt, "Left", slot="left"),
            _text(fmt, "Layouts carry slots, and a block remembers which slot "
                       "it is in even while another layout is showing.",
                  slot="left"),
            _heading(fmt, "Right", slot="right"),
            _list(fmt, ["section", "title-content", "two-column",
                        "image-left", "grid"], slot="right"),
        ]),
        _slide(fmt, "One idea per slide", "quote", [
            _text(fmt, "A deck that fits on one slide should have been an "
                       "email; a slide that needs two should have been two."),
        ]),
    ])

    review = _deck(fmt, "Quarterly review — Q1 2026", [
        _slide(fmt, "Quarterly review", "section", [
            _text(fmt, "Q1 2026"),
        ]),
        _slide(fmt, "Where the quarter went", "grid", [
            _heading(fmt, "Revenue", slot="a"),
            _text(fmt, "ƒ128,400, ahead of plan.", slot="b"),
            _heading(fmt, "Costs", slot="c"),
            _text(fmt, "ƒ91,200, in line.", slot="d"),
        ]),
        _slide(fmt, "Next quarter", "title-content", [
            _list(fmt, ["Close the ledger export",
                        "Finish the sheets viewer",
                        "Decide on hibernation billing"]),
        ]),
    ])

    return [("welcome.pxml", welcome), ("quarterly-review.pxml", review)]


class Command(IngressCommand):
    help = "Seed sample presentations into the shared Samples bucket (--full)."

    def process(self):
        if not self.full:
            return
        try:
            from toto.samples import folder, put, seeding_user
        except ImportError:
            # The samples convention is the host's. A host that ships memo
            # without it gets no samples, which is a state and not a failure.
            self.stdout.write("memo: host provides no samples bucket — skipping")
            return

        from toto.memo import presentation_format as fmt

        user = seeding_user()
        if user is None:
            self.stdout.write("memo: no admin user — skipping samples")
            return

        bucket, directory = folder(user, FOLDER)
        made = sum(int(put(user=user, bucket=bucket, directory=directory,
                           title=title, body=fmt.dumps(deck),
                           file_type="pxml"))
                   for title, deck in build(fmt))
        self.stdout.write(f"memo: {made} sample presentation(s) seeded")
