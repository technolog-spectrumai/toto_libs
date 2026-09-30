"""Who may READ a file's history and who may WRITE it (2026-09-30).

``version_views`` used to answer one question for all six doors — "may this
person work with the file?" — and answered it too widely: a folder with an
empty ACL let every signed-in account in, and a public file let every reader
take the lock, cut a version and restore an old body over its owner's work.

The doors are split now. Listing the history follows the vault's read rule
(``access.may_read``, bucket clearances first). Taking, beating and releasing
the lock, cutting a version and restoring one follow the write rule the
editors' save doors apply (``access.may_write``: the owner, the app that lends
the file out, or a superuser on the Superuser plan), and only after the read
gate. A file the caller may not see is 404; one they may see but not change is
403.
"""

import io
import json
import shutil
import subprocess
import tempfile
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault import access, locks, versions
from toto.vault.models import (Bucket, BucketClearance, FileLock, FileVersion, VaultDirectory,
                               VaultFile)
from toto.vault.plugins import VaultAccessPlugin

User = get_user_model()

WRITE_DOORS = ("lock_acquire", "lock_heartbeat", "lock_release", "version_save")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-version-doors-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.member = User.objects.create_user("member", password="pw")
        cls.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        call_command("bootstrap_plans", stdout=io.StringIO())      # root → the Superuser plan
        cls.root = User.objects.get(pk=cls.root.pk)
        # Made after the bootstrap: a superuser who never took the plan.
        cls.bare_root = User.objects.create_superuser("bareroot", "b@e.com", "pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)

    _n = 0

    def file(self, body=b"first", *, public=False, directory=None, bucket=None, owner=None):
        type(self)._n += 1
        vault_file = VaultFile(owner=owner or self.owner, title=f"doc{self._n}.txt",
                               key=f"doc-{self._n}", file_type="text", is_public=public,
                               directory=directory, bucket=bucket or self.bucket)
        vault_file.file.save(f"doc{self._n}.txt", ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def with_history(self, **kwargs):
        """A file at "second" with v1 = "first" to restore."""
        f = self.file(b"first", **kwargs)
        v1 = versions.save_version(f, author=self.owner)
        with f.file.open("wb") as handle:
            handle.write(b"second")
        return f, v1

    def body(self, vault_file):
        vault_file.refresh_from_db()
        with vault_file.file.open("rb") as handle:
            return handle.read()

    def u(self, name, vault_file, *extra):
        return reverse(f"vault:{name}", args=[vault_file.pk, *extra])

    def statuses(self, user, f, v1):
        """What every door answers ``user``, by url name."""
        self.client.force_login(user)
        out = {"version_list": self.client.get(self.u("version_list", f)).status_code}
        for name in WRITE_DOORS:
            out[name] = self.client.post(self.u(name, f)).status_code
        out["version_restore"] = self.client.post(
            self.u("version_restore", f, v1.pk)).status_code
        return out

    def assertUntouched(self, f):
        self.assertEqual(self.body(f), b"second")
        self.assertEqual(FileVersion.objects.filter(file=f).count(), 1)
        self.assertFalse(FileLock.objects.filter(file=f).exists())


class EmptyAclFolderTests(_Fixture):
    """A folder nobody put anyone on is not a folder open to everyone."""

    def setUp(self):
        self.inbox = VaultDirectory.objects.create(name="inbox", bucket=self.bucket,
                                                   owner=self.owner)

    def test_another_member_can_neither_see_lock_cut_nor_restore(self):
        f, v1 = self.with_history(directory=self.inbox)
        got = self.statuses(self.member, f, v1)
        self.assertEqual(set(got.values()), {404}, got)
        self.assertUntouched(f)

    def test_staff_is_no_one_special_there(self):
        f, v1 = self.with_history(directory=self.inbox)
        self.assertEqual(set(self.statuses(self.staff, f, v1).values()), {404})
        self.assertUntouched(f)

    def test_the_owner_still_can(self):
        f, v1 = self.with_history(directory=self.inbox)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.u("lock_acquire", f)).status_code, 200)
        self.assertEqual(self.client.post(self.u("version_save", f)).status_code, 200)
        response = self.client.post(self.u("version_restore", f, v1.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.body(f), b"first")


class PublicFileTests(_Fixture):
    """Public means anyone may read it, never that anyone may change it."""

    def test_a_reader_sees_the_history_and_is_told_it_is_read_only(self):
        f, _v1 = self.with_history(public=True)
        self.client.force_login(self.member)
        response = self.client.get(self.u("version_list", f))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([v["number"] for v in payload["versions"]], [1])
        self.assertFalse(payload["can_write"])

    def test_a_reader_is_refused_every_write_with_403(self):
        f, v1 = self.with_history(public=True)
        got = self.statuses(self.member, f, v1)
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)
        self.assertUntouched(f)

    def test_the_refusal_says_why_and_that_the_caller_cannot_write(self):
        f, v1 = self.with_history(public=True)
        self.client.force_login(self.member)
        payload = self.client.post(self.u("version_restore", f, v1.pk)).json()
        self.assertFalse(payload["can_write"])
        self.assertIn("not change it", payload["error"])

    def test_a_reader_cannot_keep_someone_elses_lock_alive_or_free_it(self):
        f = self.file(public=True)
        locks.acquire(f, self.owner)
        before = locks.holder_of(f).expires_at
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(self.u("lock_heartbeat", f)).status_code, 403)
        self.assertEqual(self.client.post(self.u("lock_release", f)).status_code, 403)
        self.assertEqual(locks.holder_of(f).expires_at, before)
        self.assertEqual(locks.holder_of(f).holder, self.owner)

    def test_staff_read_a_public_file_like_anyone_and_write_it_like_anyone(self):
        f, v1 = self.with_history(public=True)
        got = self.statuses(self.staff, f, v1)
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)
        self.assertUntouched(f)

    def test_the_owner_still_can_and_is_told_so(self):
        f, v1 = self.with_history(public=True)
        self.client.force_login(self.owner)
        self.assertTrue(self.client.get(self.u("version_list", f)).json()["can_write"])
        payload = self.client.post(self.u("lock_acquire", f)).json()
        self.assertTrue(payload["mine"])
        self.assertTrue(payload["can_write"])
        self.assertTrue(self.client.post(self.u("lock_heartbeat", f)).json()["held"])
        self.assertEqual(self.client.post(self.u("version_save", f)).status_code, 200)
        self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                         200)
        self.assertEqual(self.body(f), b"first")
        self.assertEqual(self.client.post(self.u("lock_release", f)).json(),
                         {"released": True})


class ReadersWhoAreNotWritersTests(_Fixture):
    """Every other arm of ``may_read`` reads the history and writes nothing."""

    def test_a_folder_acl_member_reads_but_does_not_write(self):
        team = VaultDirectory.objects.create(name="team", bucket=self.bucket, owner=self.owner)
        team.allowed_users.add(self.member)
        f, v1 = self.with_history(directory=team)
        got = self.statuses(self.member, f, v1)
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)
        self.assertUntouched(f)

    def test_the_buckets_owner_reads_another_persons_file_but_does_not_write(self):
        f, v1 = self.with_history(owner=self.member)
        got = self.statuses(self.owner, f, v1)       # owner of the bucket, not the file
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)


class SuperuserTests(_Fixture):
    def test_a_superuser_on_the_plan_still_can(self):
        f, v1 = self.with_history()
        self.client.force_login(self.root)
        self.assertTrue(self.client.get(self.u("version_list", f)).json()["can_write"])
        self.assertTrue(self.client.post(self.u("lock_acquire", f)).json()["mine"])
        self.assertEqual(self.client.post(self.u("version_save", f)).status_code, 200)
        self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                         200)
        self.assertEqual(self.body(f), b"first")

    def test_a_superuser_without_the_plan_reads_but_does_not_write(self):
        f, v1 = self.with_history()
        got = self.statuses(self.bare_root, f, v1)
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)
        self.assertUntouched(f)

    def test_a_superuser_without_the_plan_still_writes_their_own_file(self):
        f, v1 = self.with_history(owner=self.bare_root)
        self.client.force_login(self.bare_root)
        self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                         200)


class KeptBucketTests(_Fixture):
    """A bucket kept to a clearance hides the file — history, lock and all."""

    def setUp(self):
        self.clearance = Clearance.objects.create(name="payroll", slug="payroll")
        BucketClearance.objects.create(bucket=self.bucket, clearance=self.clearance)

    def hold(self, user):
        person, _ = Person.objects.get_or_create(user=user,
                                                 defaults={"display_name": user.username})
        person.clearances.add(self.clearance)

    def test_a_member_without_the_clearance_finds_nothing(self):
        f, v1 = self.with_history(public=True)
        self.assertEqual(set(self.statuses(self.member, f, v1).values()), {404})
        self.assertUntouched(f)

    def test_its_owner_without_the_clearance_finds_nothing_either(self):
        f, v1 = self.with_history()
        self.assertEqual(set(self.statuses(self.owner, f, v1).values()), {404})
        self.assertUntouched(f)

    def test_a_lending_app_does_not_open_a_kept_bucket(self):
        f, v1 = self.with_history()

        class Lender:
            def may_edit(self, user, vault_file):
                return True

        with patch.dict(VaultAccessPlugin.registry, {"text": Lender()}):
            self.assertEqual(set(self.statuses(self.member, f, v1).values()), {404})

    def test_a_holder_reads_and_only_the_owner_holding_it_writes(self):
        f, v1 = self.with_history()
        self.hold(self.member)
        got = self.statuses(self.member, f, v1)
        self.assertEqual(got.pop("version_list"), 200)
        self.assertEqual(set(got.values()), {403}, got)
        self.hold(self.owner)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                         200)

    def test_a_superuser_on_the_plan_passes_the_bucket(self):
        f, v1 = self.with_history()
        self.client.force_login(self.root)
        self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                         200)


class LendingAppTests(_Fixture):
    """The app that lends a file out (cyprian's wiki) still decides its writers."""

    def test_a_lent_writer_who_cannot_read_it_otherwise_still_can(self):
        f, v1 = self.with_history()

        class Lender:
            def may_edit(self, user, vault_file):
                return user.username == "member"

        with patch.dict(VaultAccessPlugin.registry, {"text": Lender()}):
            self.client.force_login(self.member)
            self.assertTrue(self.client.get(self.u("version_list", f)).json()["can_write"])
            self.assertEqual(self.client.post(self.u("lock_acquire", f)).status_code, 200)
            self.assertEqual(self.client.post(self.u("version_restore", f, v1.pk)).status_code,
                             200)
            self.assertEqual(set(self.statuses(self.staff, f, v1).values()), {404})


class MayWriteTests(_Fixture):
    """``access.may_write`` itself, beside the rule it has to agree with."""

    def test_the_arms(self):
        f = self.file(public=True)
        self.assertTrue(access.may_write(self.owner, f))
        self.assertTrue(access.may_write(self.root, f))
        for user in (self.member, self.staff, self.bare_root):
            self.assertFalse(access.may_write(user, f), user)
        self.assertFalse(access.may_write(None, f))
        self.assertFalse(access.may_write(self.owner, None))

    def test_a_writer_the_vault_would_otherwise_refuse_is_one_may_read_refuses_too(self):
        # The write rule never widens reading beyond the lending app: every
        # other writer is somebody may_read already lets in.
        f = self.file()
        for user in (self.owner, self.root):
            self.assertTrue(access.may_read(user, f))

    def test_a_broken_lending_app_refuses(self):
        f = self.file()

        class Broken:
            def may_edit(self, user, vault_file):
                raise RuntimeError("down")

        with patch.dict(VaultAccessPlugin.registry, {"text": Broken()}):
            self.assertFalse(access.may_write(self.member, f))


class ChainTests(_Fixture):
    """A refused write is on the audit chain, as the door's other outcomes are."""

    def rows(self, action):
        return AuditRecord.objects.filter(app_label="vault", action=action)

    def test_a_refused_restore_is_recorded_as_a_failure_without_the_body(self):
        f, v1 = self.with_history(public=True)
        self.client.force_login(self.member)
        self.client.post(self.u("version_restore", f, v1.pk))
        entry = self.rows("FILE_RESTORED").get()
        self.assertFalse(entry.success)
        self.assertEqual(entry.metadata["status"], 403)
        self.assertEqual(entry.actor_user, self.member)
        self.assertNotIn("first", json.dumps(entry.metadata))

    def test_a_refused_lock_claim_is_recorded_once(self):
        f = self.file(public=True)
        self.client.force_login(self.member)
        self.client.post(self.u("lock_acquire", f))
        entry = self.rows("FILE_LOCK_REFUSED").get()
        self.assertFalse(entry.success)
        self.assertEqual((entry.object_id, entry.metadata["status"]), (str(f.pk), 403))

    def test_a_claim_on_a_file_somebody_is_editing_is_not_a_refusal(self):
        # 423 is contention between two writers, not a security refusal: it
        # would otherwise flood the chain every time two people open a file.
        f = self.file()
        locks.acquire(f, self.root)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.u("lock_acquire", f)).status_code, 423)
        self.assertFalse(self.rows("FILE_LOCK_REFUSED").exists())

    def test_a_granted_lock_and_heartbeats_stay_off_the_chain(self):
        f = self.file()
        self.client.force_login(self.owner)
        self.client.post(self.u("lock_acquire", f))
        self.client.post(self.u("lock_heartbeat", f))
        self.client.post(self.u("lock_release", f))
        self.assertFalse(AuditRecord.objects.filter(app_label="vault",
                                                    object_id=str(f.pk)).exists())


class PanelTemplateTests(SimpleTestCase):
    """oya/_file_versions.html honours ``can_write`` (accepted and ignored until
    2026-09-30, so a reader's page claimed the lock of a file it only showed)."""

    def render(self, **context):
        return render_to_string("oya/_file_versions.html", {"file_pk": 7, **context})

    def test_the_page_says_first_and_writable_is_the_default(self):
        self.assertIn("fileVersions(7, true)", self.render())
        self.assertIn("fileVersions(7, true)", self.render(can_write=True))
        self.assertIn("fileVersions(7, false)", self.render(can_write=False))
        # A caller whose own flag is missing passes "" — still the default.
        self.assertIn("fileVersions(7, true)", self.render(can_write=""))

    def test_save_and_restore_are_writers_only(self):
        body = self.render(can_write=False)
        self.assertIn('x-show="canWrite" class="flex flex-wrap', body)
        self.assertIn('<template x-if="canWrite">', body)
        self.assertLess(body.index('<template x-if="canWrite">'), body.index("restore(item)"))
        self.assertIn("you may not change it", body)

    def test_the_banner_promises_a_turn_only_to_somebody_who_could_take_it(self):
        body = render_to_string("oya/_lock_banner.html")
        self.assertIn('x-show="lock.can_write !== false"', body)
        self.assertLess(body.index('x-show="lock.can_write !== false"'),
                        body.index("your changes will not save until they close it"))


_HARNESS = r"""
const [script, canWrite, answersJson] = process.argv.slice(1);
const answers = JSON.parse(answersJson);
const calls = [], events = [], listeners = {}, intervals = [];
const nav = { sendBeacon: (url) => { calls.push(["BEACON", url]); return true; } };
Object.defineProperty(globalThis, "navigator", { value: nav, configurable: true });
globalThis.window = {
  navigator: nav,
  addEventListener: (name, fn) => { listeners[name] = fn; },
  dispatchEvent: (event) => { events.push(event.detail); },
  confirm: () => true,
  location: { reload() {} },
};
globalThis.document = { cookie: "" };
globalThis.CustomEvent = function (name, init) { this.detail = init.detail; };
globalThis.setInterval = (fn) => { intervals.push(fn); return intervals.length; };
globalThis.clearInterval = (id) => { intervals[id - 1] = null; };
globalThis.fetch = (url, opts) => {
  const method = (opts && opts.method) || "GET";
  calls.push([method, url]);
  const a = answers[method + " " + url] || { status: 404, body: null };
  return Promise.resolve({
    status: a.status, ok: a.status < 400,
    json: () => a.body === null ? Promise.reject(new Error("not json")) : Promise.resolve(a.body),
  });
};
require(script);
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));
(async () => {
  const panel = window.fileVersions(7, canWrite === "true");
  panel.init();
  await settle();
  intervals.filter(Boolean).forEach((fn) => fn());
  await settle();
  intervals.filter(Boolean).forEach((fn) => fn());
  await settle();
  if (listeners.pagehide) listeners.pagehide();
  await panel.saveVersion();
  await panel.restore({ id: 1, number: 1 });
  await settle();
  console.log(JSON.stringify({ calls, canWrite: panel.canWrite,
                               last: events[events.length - 1] || null }));
})();
"""


@skipUnless(shutil.which("node"), "node is not installed")
class PanelScriptTests(SimpleTestCase):
    """oya/file_versions.js, run under node with the vault's answers faked: a
    reader's panel sends nothing the write doors would refuse."""

    LIST = "GET /vault/file/7/versions/"
    LOCK = "POST /vault/file/7/lock/"
    BEAT = "POST /vault/file/7/lock/beat/"

    def run_panel(self, can_write, answers):
        from django.contrib.staticfiles import finders

        script = finders.find("oya/file_versions.js")
        done = subprocess.run(
            ["node", "-e", _HARNESS, script, "true" if can_write else "false",
             json.dumps(answers)],
            capture_output=True, text=True, timeout=30, check=True)
        return json.loads(done.stdout)

    def state(self, can_write, **extra):
        return {"locked": False, "mine": False, "holder": "", "heartbeat_seconds": 30,
                "can_write": can_write, **extra}

    def test_a_page_that_says_read_only_only_reads(self):
        out = self.run_panel(False, {self.LIST: {"status": 200, "body": {
            "versions": [], **self.state(False)}}})
        self.assertEqual(out["calls"], [["GET", "/vault/file/7/versions/"]])
        self.assertFalse(out["canWrite"])
        self.assertFalse(out["last"]["can_write"])

    def test_a_refused_claim_turns_the_panel_into_a_readers(self):
        out = self.run_panel(True, {
            self.LOCK: {"status": 403, "body": {"error": "no", "can_write": False}},
            self.LIST: {"status": 200, "body": {"versions": [], **self.state(False)}}})
        self.assertEqual(out["calls"], [["POST", "/vault/file/7/lock/"],
                                        ["GET", "/vault/file/7/versions/"]])
        self.assertFalse(out["canWrite"])

    def test_a_writer_claims_beats_saves_restores_and_lets_go(self):
        out = self.run_panel(True, {
            self.LOCK: {"status": 200, "body": self.state(True, locked=True, mine=True)},
            self.BEAT: {"status": 200, "body": {"held": True, **self.state(True)}},
            self.LIST: {"status": 200, "body": {"versions": [], **self.state(True)}},
            "POST /vault/file/7/versions/save/": {"status": 200, "body": {"saved": True}},
            "POST /vault/file/7/versions/1/restore/": {"status": 200, "body": {}}})
        methods = [tuple(call) for call in out["calls"]]
        self.assertEqual(methods.count(("POST", "/vault/file/7/lock/beat/")), 2)
        self.assertIn(("BEACON", "/vault/file/7/lock/release/"), methods)
        self.assertIn(("POST", "/vault/file/7/versions/save/"), methods)
        self.assertIn(("POST", "/vault/file/7/versions/1/restore/"), methods)
        self.assertTrue(out["canWrite"])

    def test_a_refused_heartbeat_stops_the_beat_and_the_buttons(self):
        out = self.run_panel(True, {
            self.LOCK: {"status": 200, "body": self.state(True, locked=True, mine=True)},
            self.BEAT: {"status": 403, "body": {"error": "no", "can_write": False}},
            self.LIST: {"status": 200, "body": {"versions": [], **self.state(True)}}})
        methods = [tuple(call) for call in out["calls"]]
        self.assertEqual(methods.count(("POST", "/vault/file/7/lock/beat/")), 1)
        self.assertNotIn(("BEACON", "/vault/file/7/lock/release/"), methods)
        self.assertNotIn(("POST", "/vault/file/7/versions/save/"), methods)
        self.assertFalse(out["canWrite"])
        self.assertFalse(out["last"]["can_write"])
