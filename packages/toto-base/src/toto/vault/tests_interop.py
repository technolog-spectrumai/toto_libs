"""Interop check — the REAL strategy, not a helper.

Encrypts through `TextStrategy` exactly as the platform does, and leaves the
ciphertext plus the strongbox parameters where the desktop app's test can pick
them up. The reverse test opens what the desktop wrote.
"""
import base64
import json
import os

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from toto.gervazy.models import UserStrongbox
from toto.vault.models import Bucket, VaultFile
from toto.vault.strategy.text import TextStrategy

SP = "/tmp/claude-1000/-home-janek-Desktop-dev-toto-project/5d703054-97c1-44d4-bd76-f5102522cac1/scratchpad"
PASSWORD = "interop-password"
BODY = b"through the real strategy\n"

User = get_user_model()


class InteropTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("interop", password="x")
        self.box = UserStrongbox.objects.create(owner=self.user, name="sb")
        self.bucket = Bucket.objects.create(name="ib", owner=self.user, slug="ib")

    def _file(self, data: bytes) -> VaultFile:
        vf = VaultFile.objects.create(
            owner=self.user, bucket=self.bucket, title="t.txt",
            key="interop-t", file_type="text")
        vf.file.save("t.txt", ContentFile(data), save=True)
        return vf

    def test_a_writes_what_the_desktop_must_open(self):
        vf = self._file(BODY)
        TextStrategy().encrypt(vf, PASSWORD)
        vf.refresh_from_db()
        self.assertTrue(vf.is_encrypted)
        blob = vf.file.read()
        with open(os.path.join(SP, "strategy_out.bin"), "wb") as fh:
            fh.write(blob)
        with open(os.path.join(SP, "strategy_params.json"), "w") as fh:
            json.dump({
                "password": PASSWORD,
                "salt_b64": base64.b64encode(bytes(self.box.salt)).decode(),
                "memory_cost": self.box.argon2_memory_cost,
                "iterations": self.box.argon2_iterations,
                "lanes": self.box.argon2_lanes,
                "plaintext": BODY.decode(),
            }, fh)
        self.assertTrue(blob.startswith(b"TOTOSEAL"), blob[:12])

    def test_b_opens_what_the_desktop_wrote(self):
        path = os.path.join(SP, "desktop_out.bin")
        if not os.path.exists(path):
            self.skipTest("run the desktop half first")
        params = json.load(open(os.path.join(SP, "strategy_params.json")))
        # Reuse THIS user's strongbox by forcing the desktop's parameters onto
        # it, so the decrypt runs through the strategy's own code path.
        UserStrongbox.objects.filter(pk=self.box.pk).update(
            salt=base64.b64decode(params["salt_b64"]),
            argon2_memory_cost=params["memory_cost"],
            argon2_iterations=params["iterations"],
            argon2_lanes=params["lanes"])
        vf = self._file(open(path, "rb").read())
        VaultFile.objects.filter(pk=vf.pk).update(is_encrypted=True)
        vf.refresh_from_db()
        TextStrategy().decrypt(vf, params["password"])
        vf.refresh_from_db()
        self.assertEqual(vf.file.read(), b"from the desktop\n")
        self.assertFalse(vf.is_encrypted)
