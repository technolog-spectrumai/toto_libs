"""Interop check — the REAL strategy, not a helper.

Encrypts through `TextStrategy` exactly as the platform does and leaves the
frame on disk for the desktop's crate to open; the reverse tests open, through
the same strategy, frames the desktop sealed.

The desktop half is `gervasius::gervazy::cross_language` in the enigma monorepo
(`crates/rotor/gervasius/src/gervazy.rs`). It reads no parameter file: its
password, salt, Argon2 costs, AAD and both plaintexts are constants, and the
constants below are the same values — change one side, change the other.
Zinnia's half (`platform_interop` in `src-tauri/src/lock.rs`, which read
`strategy_params.json` from `$INTEROP_DIR`) was deleted on 2026-08-25 when
zinnia moved its local seal to ZINNLOCK, and zinnia itself is closed.

Both directions are gated. Python -> Rust: the forward test checks its frame
opens from the constants alone, which is all the desktop has. Rust -> Python:
`DESKTOP_FRAME` is a frame the crate sealed once, and with the salt fixed it
opens forever. A frame the crate seals FRESH is checked when
`$TOTO_INTEROP_DIR` holds one (the exchange directory, else a temporary one).
The whole loop, by hand:

    export TOTO_INTEROP_DIR="$(mktemp -d)"
    manage.py test toto.vault.tests_interop     # writes strategy_out.bin
    export SHARED_IN="$TOTO_INTEROP_DIR/strategy_out.bin"
    export SHARED_OUT="$TOTO_INTEROP_DIR/desktop_out.bin"
    (cd <enigma>/crates/rotor &&
     cargo test -p gervasius --features gervazy -- --ignored cross_language)
    manage.py test toto.vault.tests_interop     # now opens desktop_out.bin too

The salt is fixed, not the strongbox's random default, on purpose. With a fresh
salt per run, the second `manage.py test` above re-sealed under a NEW salt
before the reverse test ran, so a frame the desktop had sealed under the old
salt could never open and the loop could not close.
"""
import os
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from toto.gervazy import sealed
from toto.gervazy.models import UserStrongbox
from toto.vault.models import Bucket, VaultFile
from toto.vault.strategy.text import TextStrategy

#: `cross_language`'s constants: "correct horse", `(0u8..16)`, 65536 KiB /
#: 3 passes / 4 lanes (also the strongbox defaults), AAD `toto:vault:file:v1`.
PASSWORD = "correct horse"
SALT = bytes(range(16))
MEMORY_COST, ITERATIONS, LANES = 65536, 3, 4
TO_DESKTOP = b"python sealed with the shared frame\n"
FROM_DESKTOP = b"rust sealed with the shared frame\n"

#: What `cross_language::writes_a_frame_python_can_open` wrote on 2026-09-23
#: (enigma gervasius at a0078296). The salt and constants are fixed, so it opens
#: forever: the Rust->Python direction is gated even on a box with no cargo.
DESKTOP_FRAME = bytes.fromhex(
    "544f544f5345414c0154a104d194c57b84c4a38410f39506f318ae1c5ad1d535"
    "b8c832d5de720e0f2052ed64095af7d1b722400db925e21d483f71715177532d"
    "166622ca9f42d8176069755a523fea0885f0d8")

#: The desktop's `SHARED_IN` and `SHARED_OUT`.
STRATEGY_OUT = "strategy_out.bin"
DESKTOP_OUT = "desktop_out.bin"

# A MEDIA_ROOT of its own, as in tests_api and its siblings: the checkout's
# media dir is a root-owned bind mount. VAULT_ROOT too, so a host that puts
# vault bytes on another disk cannot send these there.
_MEDIA = tempfile.mkdtemp(prefix="vault-interop-")

User = get_user_model()


@override_settings(MEDIA_ROOT=_MEDIA, VAULT_ROOT=None)
class InteropTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.addClassCleanup(shutil.rmtree, _MEDIA, ignore_errors=True)
        cls.dir = os.environ.get("TOTO_INTEROP_DIR")
        if cls.dir:
            os.makedirs(cls.dir, exist_ok=True)
        else:
            cls.dir = tempfile.mkdtemp(prefix="toto-interop-")
            cls.addClassCleanup(shutil.rmtree, cls.dir, ignore_errors=True)

    def setUp(self):
        self.user = User.objects.create_user("interop", password="x")
        UserStrongbox.objects.create(
            owner=self.user, name="sb", salt=SALT,
            argon2_memory_cost=MEMORY_COST, argon2_iterations=ITERATIONS,
            argon2_lanes=LANES)
        self.bucket = Bucket.objects.create(name="ib", owner=self.user, slug="ib")

    def _file(self, data: bytes) -> VaultFile:
        vf = VaultFile.objects.create(
            owner=self.user, bucket=self.bucket, title="t.txt",
            key="interop-t", file_type="text")
        vf.file.save("t.txt", ContentFile(data), save=True)
        return vf

    def _open_through_strategy(self, frame: bytes) -> bytes:
        self.assertTrue(sealed.is_current(frame),
                        "the desktop wrote a frame this side does not recognise")
        vf = self._file(frame)
        VaultFile.objects.filter(pk=vf.pk).update(is_encrypted=True)
        vf.refresh_from_db()
        TextStrategy().decrypt(vf, PASSWORD)
        vf.refresh_from_db()
        self.assertFalse(vf.is_encrypted)
        with vf.file.open("rb") as fh:
            return fh.read()

    def test_the_strategy_writes_what_the_desktop_opens(self):
        vf = self._file(TO_DESKTOP)
        TextStrategy().encrypt(vf, PASSWORD)
        vf.refresh_from_db()
        self.assertTrue(vf.is_encrypted)
        with vf.file.open("rb") as fh:
            blob = fh.read()

        # What the desktop checks first: the bytes say what they are.
        self.assertTrue(sealed.is_current(blob), blob[:12])
        self.assertNotIn(TO_DESKTOP.strip(), blob)
        # And what it does next, with the constants and nothing else — no
        # strongbox row, no database. If this fails, the desktop fails too.
        self.assertEqual(
            sealed.open_frame(PASSWORD, SALT, blob, memory_cost=MEMORY_COST,
                              iterations=ITERATIONS, lanes=LANES,
                              aad=sealed.VAULT_AAD),
            TO_DESKTOP)

        out = os.path.join(self.dir, STRATEGY_OUT)
        with open(out, "wb") as fh:
            fh.write(blob)
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(), blob)

    def test_the_strategy_opens_a_frame_the_desktop_sealed_once(self):
        self.assertEqual(self._open_through_strategy(DESKTOP_FRAME), FROM_DESKTOP)

    def test_the_strategy_opens_what_the_desktop_wrote(self):
        path = os.path.join(self.dir, DESKTOP_OUT)
        if not os.path.exists(path):
            self.skipTest(
                f"no {DESKTOP_OUT} in {self.dir}: set TOTO_INTEROP_DIR and run "
                "the desktop half (see this module's docstring)")
        with open(path, "rb") as fh:
            frame = fh.read()
        self.assertEqual(self._open_through_strategy(frame), FROM_DESKTOP)
