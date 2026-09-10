"""Which image a family is allowed to run, pinned by digest.

THE ATTACK THIS IS FOR. A runner image is where a job's dependencies live —
texlive, ffmpeg, an interpreter. An image that was quietly replaced between
being built and being run would execute somebody else's code inside every Gear
on the host, with the platform's own hardening applied to it and nothing in the
logs to say anything changed. Pinning by digest is the difference between "the
image called anastasia-latex" and "these exact bytes".

WHAT THIS DOES NOT CLAIM TO DO. It is not a supply-chain guarantee: the lock
file is generated from what was built, so it attests that the image running is
the image the build produced — not that the build produced the right thing.
That is the honest scope, and the distinction matters because a stronger claim
would invite skipping the review that actually catches a bad Dockerfile.

AN UNPINNED IMAGE IS ALLOWED, and that is a deliberate default rather than an
oversight. Locally-built images carry no RepoDigest at all, which is every
runner image on a host that builds its own; refusing those would break the
ordinary deployment to defend against an exposure it does not have. A lock file
that NAMES a family and disagrees is refused; one that says nothing is silent.

Django-free.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("toto.anastasia.executor.images")

#: Written by deploy/anastasia/build.sh beside the images it builds.
DEFAULT_LOCK = "/etc/anastasia/images.lock.json"


class ImageMismatch(Exception):
    """The image on this host is not the one the lock file names."""


def load(path: str | None = None) -> dict:
    """`{family: digest}`, or {} when there is no lock file.

    A missing file is not an error: a host that has never generated one runs
    unpinned, which is what every host does today.
    """
    path = path or os.environ.get("ANASTASIA_IMAGE_LOCK", DEFAULT_LOCK)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        # A CORRUPT lock file is NOT read as "no lock". The whole point is to
        # refuse an image somebody swapped, and treating an unparseable file as
        # permission to run anything would make corrupting it the attack.
        raise ImageMismatch(
            f"the image lock at {path} could not be read ({exc}), so no runner "
            "image can be trusted") from exc

    out = {}
    for family, entry in (data or {}).items():
        digest = entry.get("digest") if isinstance(entry, dict) else entry
        if digest:
            out[str(family)] = str(digest)
    return out


def verify(driver, family_key: str, image: str, lock: dict | None = None) -> None:
    """Refuse a family whose image is not what the lock file names.

    Called before staging anything, so a mismatch costs a refusal rather than a
    tar unpacked into a container that is about to be destroyed.
    """
    lock = load() if lock is None else lock
    expected = lock.get(family_key)
    if not expected:
        return                                   # unpinned family: allowed
    actual = ""
    try:
        actual = driver.image_digest(image)
    except Exception as exc:                     # noqa: BLE001
        raise ImageMismatch(
            f"the digest of {image} could not be read ({exc})") from exc
    if not actual:
        raise ImageMismatch(
            f"{image} carries no digest, but the image lock pins {family_key} "
            f"to {expected[:19]}…")
    if actual != expected:
        log.error("anastasia: %s is %s, lock says %s", image, actual, expected)
        raise ImageMismatch(
            f"the {family_key} runner image on this host is not the one this "
            "deployment was built with. Nothing has been run.")
