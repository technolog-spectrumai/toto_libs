"""Hibernation: what goes to zero, what comes back, and what refuses.

The brief asks for tampering, missing bases, partial snapshots, concurrency,
zero hibernation billing and exact restoration. Each has a test here, and where
one CANNOT be honestly asserted that is said out loud rather than faked:

* **Zero billing** is asserted as zero ALLOCATION — `services.booked()` falling
  to nothing — because reserved capacity is not billed on this deployment at
  all. `anastasia.execution` is explicitly "a rate limit on submissions, not a
  price on compute", and the reservation levy `anastasia.capsule_hour` is still a
  TODO with no taxes.py. A test asserting "the charge is zero" would pass
  against an engine that never charges and prove nothing; asserting the pool is
  free proves the thing that actually matters.
* **A kept home** is contributed by a FAKE LAB registered for this test, not by
  dracena. That is not convenience: since 2026-09-10 **no installed lab keeps a
  home at all**, so exercising this through a real one would mean exercising
  nothing. See `NoLabKeepsAHomeTests` below, which pins that fact so it is a
  decision rather than a silence.

  What is faked is only the lab hook. What is NOT faked: the packing, the
  digest, the size cap, the tamper refusal, the release and the staging — every
  guarantee this module actually makes.
"""

from __future__ import annotations

from unittest import mock

from django.test import override_settings
from django.urls import reverse

from toto.ambrosia import hibernation
from toto.ambrosia.models import WorkspaceHibernation, WorkspaceKind
from toto.ambrosia.tests.base import AmbrosiaTestCase
from toto.anastasia import services as capsule_services
from toto.anastasia.limits import Limits
from toto.anastasia.models import ComputeLease
from toto.dracena.tests.fakes import POOL, FakeRunBackend


@override_settings(
    ANASTASIA_POOL=POOL,
    ANASTASIA_RUNTIME_BACKEND="toto.dracena.tests.fakes.FakeRunBackend",
)
class HibernationTestCase(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        FakeRunBackend.reset()
        self.ws = self.make_workspace(name="Py", kind=WorkspaceKind.PYTHON)

    def give_a_capsule(self, *, permanent_home=False, name="py"):
        lease = capsule_services.reserve(
            owner=self.owner, name=name, limits=Limits(2000, 2048, 1024, 512),
            permanent_home=permanent_home)
        capsule_services.mount(lease=lease, actor=self.owner)
        return lease

    def lab_that_keeps(self, home_files):
        """Install a lab whose snapshot contributes `home_files`, for this test.

        WorkspaceApp is a frozen dataclass, so a hook is swapped by replacing
        the registry entry rather than by patching an attribute — the same
        move PartialSnapshotTests makes.

        Called `start_kernel_on(lease)` until 2026-09-10, when it created a real
        Execution row and started dracena's kernel against a mocked
        `jobs.start_runtime`. There is no kernel, no `start_runtime` and no
        long-lived runtime to collect from. What the hybrid tests are actually
        about — pack, digest, cap, refuse, stage — never needed one; it needed
        a lab that hands over some bytes, which is what this is.
        """
        from toto.ambrosia import registry

        app = registry.for_kind(WorkspaceKind.PYTHON)
        self.addCleanup(registry._BY_KIND.__setitem__, WorkspaceKind.PYTHON, app)

        def _snapshot(workspace, *, permanent_home=False):
            # The permanent_home gate is the LAB'S to honour — the base passes
            # the flag and trusts the answer — so the fake honours it too.
            snapshot = {"runtime": {"image": "anastasia-python"}}
            if permanent_home:
                # KEYS ARRIVE WITHOUT THE `home/` PREFIX. `staged_home` puts it
                # back (`f"{HOME_NAME}/{name}"`), so a lab that leaves it on
                # produces `home/home/.gitconfig` on the way out. The real hook
                # stripped it — `name[len("home/"):]` — because it read out of
                # a container's `/out/home`; the caller here passes the names as
                # they appear on disk, so the fake strips the same way.
                snapshot["home_files"] = {
                    name[len(f"{hibernation.HOME_NAME}/"):]: body
                    for name, body in home_files.items()}
            return snapshot

        registry._BY_KIND[WorkspaceKind.PYTHON] = registry.WorkspaceApp(
            namespace=app.namespace, kind=app.kind,
            extra_context=app.extra_context, extra_urls=app.extra_urls,
            teardown=app.teardown, main_id_for=app.main_id_for,
            settings_fields=app.settings_fields,
            settings_template=app.settings_template,
            room_panels=app.room_panels,
            snapshot=_snapshot, restore=app.restore)


class ManifestHibernationTests(HibernationTestCase):

    def test_hibernating_writes_down_what_it_is(self):
        """The BASE's half of the manifest, which is now the whole of it.

        This also asserted `manifest["runtime"]["image"]` until 2026-09-10 —
        the lab's own half, recording the image to rebuild on. Dracena
        registers no snapshot hook any more, so no lab contributes anything
        here and the key is absent. Asserted as an absence rather than
        deleted: a manifest that silently regrew a lab section would mean a
        long-lived runtime came back without anyone deciding it should.
        """
        self.give_a_capsule()
        manifest = hibernation.hibernate(self.ws, user=self.owner)
        self.assertEqual(manifest["kind"], WorkspaceKind.PYTHON)
        self.assertEqual(manifest["namespace"], "dracena")
        self.assertEqual(manifest["depth"], "manifest")
        self.assertNotIn("runtime", manifest)
        self.assertNotIn("error", manifest)
        self.assertTrue(hibernation.is_hibernated(self.ws))

    def test_it_is_idempotent(self):
        """A double-click is not an error."""
        self.give_a_capsule()
        first = hibernation.hibernate(self.ws, user=self.owner)
        second = hibernation.hibernate(self.ws, user=self.owner)
        self.assertEqual(first, second)
        self.assertEqual(WorkspaceHibernation.objects.count(), 1)

    def test_waking_something_awake_is_not_an_error_either(self):
        hibernation.rehydrate(self.ws, user=self.owner)
        self.assertFalse(hibernation.is_hibernated(self.ws))

    def test_a_round_trip_clears_the_sleeping_state(self):
        self.give_a_capsule()
        hibernation.hibernate(self.ws, user=self.owner)
        hibernation.rehydrate(self.ws, user=self.owner)
        self.assertFalse(hibernation.is_hibernated(self.ws))
        record = WorkspaceHibernation.objects.get(workspace=self.ws)
        self.assertIsNotNone(record.rehydrated_at)


class ComputeGoesToZeroTests(HibernationTestCase):

    def test_the_pool_is_free_afterwards(self):
        """The point of the whole feature.

        Asserted on `booked()` rather than on a charge: this deployment does not
        bill a reservation at all (see the module docstring), so allocation is
        the honest measure and the one the pool actually enforces.
        """
        self.give_a_capsule()
        before = capsule_services.booked()
        self.assertGreater(before.cpu_millicores, 0)

        hibernation.hibernate(self.ws, user=self.owner)

        after = capsule_services.booked()
        self.assertEqual(after.cpu_millicores, 0)
        self.assertEqual(after.ram_mb, 0)
        self.assertEqual(after.scratch_mb, 0)
        self.assertEqual(after.pids, 0)

    def test_it_releases_rather_than_merely_unmounting(self):
        """Unmounting frees NOTHING: booked() counts every OPEN lease, mounted
        or not. This is the distinction the whole design turns on."""
        lease = self.give_a_capsule()
        hibernation.hibernate(self.ws, user=self.owner)
        lease.refresh_from_db()
        self.assertIsNotNone(lease.released_at)
        self.assertFalse(lease.is_open())

    def test_the_manifest_records_that_it_let_go(self):
        self.give_a_capsule()
        manifest = hibernation.hibernate(self.ws, user=self.owner)
        self.assertTrue(manifest["lease_released"])

    def test_a_workspace_with_no_capsule_still_hibernates(self):
        """Nothing to release is not a failure — it is already at zero."""
        manifest = hibernation.hibernate(self.ws, user=self.owner)
        self.assertTrue(hibernation.is_hibernated(self.ws))
        self.assertFalse(manifest["lease_released"])


class HybridHibernationTests(HibernationTestCase):
    """The permanent-home half — the depth chosen when the Capsule was reserved."""

    HOME = {"home/.ipython/history.sqlite": b"SQLite format 3\\x00",
            "home/.gitconfig": b"[user]\\n\\tname = Ada\\n"}

    def _hibernate_with_home(self, files=None):
        self.lab_that_keeps(self.HOME if files is None else files)
        lease = self.give_a_capsule(permanent_home=True)
        return lease, hibernation.hibernate(self.ws, user=self.owner)

    def test_a_permanent_home_capsule_keeps_the_home(self):
        _lease, manifest = self._hibernate_with_home()
        self.assertEqual(manifest["depth"], "hybrid")
        record = WorkspaceHibernation.objects.get(workspace=self.ws)
        self.assertTrue(record.home_digest)
        self.assertGreater(record.home_bytes, 0)

    def test_hibernation_reads_the_capsule_the_workspace_is_pinned_to(self):
        """TWO GEARS, and the workspace names the SECOND one.

        `_lease_for` filtered on the owner alone and took the oldest open lease
        until 2026-09-10, which is right only for an account holding exactly
        one Capsule. Here the older Capsule is ordinary and the pinned one keeps a
        home, so reading the wrong lease silently downgrades the hibernation to
        a manifest and throws away a $HOME the user reserved capacity to keep.

        The companion failure is worse and is asserted below: `hibernate`
        defaults to `release_lease=True`, so the old code also RELEASED the
        older Capsule — one the user never mentioned, possibly running work.
        """
        from toto.ambrosia import capsules as capsules_module

        older = self.give_a_capsule(permanent_home=False, name="first")
        pinned = self.give_a_capsule(permanent_home=True, name="second")
        self.assertLess(older.created_at, pinned.created_at)

        self.lab_that_keeps(self.HOME)
        with mock.patch.object(capsules_module, "preferred",
                               return_value=str(pinned.uuid)):
            manifest = hibernation.hibernate(self.ws, user=self.owner)

        self.assertEqual(manifest["depth"], "hybrid",
                         "the pinned Capsule keeps a home; reading the older "
                         "lease reports 'manifest' and drops it")
        self.assertTrue(manifest["permanent_home"])

        older.refresh_from_db()
        self.assertIsNone(
            older.released_at,
            "hibernating a workspace pinned to another Capsule must not release "
            "this one")

    def test_an_ordinary_capsule_keeps_no_home_even_if_one_exists(self):
        """The choice is the Capsule's, made when it was reserved.

        The base passes `permanent_home` to the lab and takes its word for it,
        so this pins BOTH halves of that contract: the flag arrives false, and
        nothing is kept when it does.
        """
        self.lab_that_keeps(self.HOME)
        self.give_a_capsule(permanent_home=False)
        manifest = hibernation.hibernate(self.ws, user=self.owner)
        self.assertFalse(manifest["permanent_home"])
        self.assertEqual(manifest["depth"], "manifest")
        record = WorkspaceHibernation.objects.get(workspace=self.ws)
        self.assertFalse(record.home_digest)

    def test_the_kept_home_is_staged_back_exactly(self):
        """Exact restoration: the same bytes, under the same names."""
        self._hibernate_with_home()
        staged = hibernation.staged_home(self.ws)
        self.assertEqual(staged, self.HOME)

    def test_the_kept_home_survives_a_wake_and_is_still_offered(self):
        """WHAT IT NO LONGER DOES, said plainly.

        This asserted that `kernel.start` staged the kept home into the
        runtime's inputs with `persistent_home=True`. Both are deleted: there
        is no kernel start, and `staged_home()` has no production caller at
        all since 2026-09-10 — a Run is a fresh process and puts nothing back.

        What is still true and still worth pinning is that waking does not
        DESTROY what was kept: the bytes verify and `staged_home` still hands
        them over. That is what a future consumer — antaresia, or a per-run
        home carried through `inputs=` — would build on, and it is the reason
        the blob is not deleted on wake.
        """
        self._hibernate_with_home()
        hibernation.rehydrate(self.ws, user=self.owner)
        self.assertFalse(hibernation.is_hibernated(self.ws))
        self.assertEqual(hibernation.staged_home(self.ws), self.HOME)

    def test_an_oversized_home_is_reported_rather_than_silently_dropped(self):
        huge = {"home/big.bin": b"x" * 2048}
        with mock.patch.object(hibernation, "MAX_HOME_BYTES", 10):
            _lease, manifest = self._hibernate_with_home(huge)
        self.assertIn("home_skipped", manifest)
        self.assertEqual(manifest["depth"], "manifest")

    def test_a_runtime_that_wrote_no_home_is_not_an_error(self):
        _lease, manifest = self._hibernate_with_home({})
        self.assertEqual(manifest["depth"], "manifest")
        self.assertTrue(hibernation.is_hibernated(self.ws))


class TamperingTests(HibernationTestCase):
    """A home is executable state — shell profiles, a pip --user tree."""

    def _sleep_with_home(self):
        self.lab_that_keeps({"home/.bashrc": b"echo hello\\n"})
        self.give_a_capsule(permanent_home=True)
        hibernation.hibernate(self.ws, user=self.owner)
        return WorkspaceHibernation.objects.get(workspace=self.ws)

    def test_a_flipped_byte_refuses_the_restore(self):
        record = self._sleep_with_home()
        record.home_digest = "0" * 64          # what a changed blob looks like
        record.save(update_fields=["home_digest"])

        with self.assertRaises(hibernation.HibernationError) as caught:
            hibernation.rehydrate(self.ws, user=self.owner)
        self.assertIn("does not match", str(caught.exception))

    def test_a_tampered_home_is_never_staged_either(self):
        """The check is on every path, not only on an explicit wake.

        `staged_home` is the second door. It has no production caller today
        (it was `kernel.start`'s), so this is the test keeping the refusal
        honest until one returns — which is exactly when a missing check would
        cost something."""
        record = self._sleep_with_home()
        record.home_digest = "0" * 64
        record.save(update_fields=["home_digest"])
        self.assertEqual(hibernation.staged_home(self.ws), {})

    def test_a_refused_restore_loses_nothing_else(self):
        record = self._sleep_with_home()
        record.home_digest = "0" * 64
        record.save(update_fields=["home_digest"])
        with self.assertRaises(hibernation.HibernationError):
            hibernation.rehydrate(self.ws, user=self.owner)
        # Still asleep, still recoverable once somebody looks at it.
        self.assertTrue(hibernation.is_hibernated(self.ws))

    def test_a_corrupt_archive_is_nothing_rather_than_a_crash(self):
        self.assertEqual(hibernation.unpack(b"not a tar at all"), {})

    def test_a_traversing_member_is_dropped(self):
        """Names come out of an archive and are handed to something that stages
        them, so they are filtered at the door."""
        blob = hibernation.pack({"home/ok": b"1"})
        self.assertIn("home/ok", hibernation.unpack(blob))
        # Built by hand, because pack() would never produce these.
        import io
        import tarfile
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name in ("../escape", "/etc/passwd", "home/fine"):
                info = tarfile.TarInfo(name)
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
        out = hibernation.unpack(buffer.getvalue())
        self.assertEqual(set(out), {"home/fine"})


class MissingBaseTests(HibernationTestCase):
    """What happens when the thing it was hibernated against is gone."""

    def test_a_changed_home_refuses_the_wake(self):
        """The refusal MOVED on 2026-09-10 rather than lapsing.

        This asserted that a workspace whose stored PACKAGE TREE had changed
        digest refused to wake — dracena's `_restore` hook raised, and the
        error surfaced as a HibernationError. The library installer is deleted,
        so there is no package tree, no environment manifest and no restore
        hook; that refusal cannot fire.

        The claim it was really making — a workspace comes back WHOLE or says
        it cannot — is unchanged, and `hibernation.rehydrate` still enforces it
        over the one thing still kept: the home directory. A home is executable
        state (shell profiles, a `pip --user` tree), so restoring bytes that do
        not match what was stored is the failure worth refusing.
        """
        self.lab_that_keeps({"home/.bashrc": b"echo hello\n"})
        self.give_a_capsule(permanent_home=True)
        hibernation.hibernate(self.ws, user=self.owner)

        record = hibernation.record_for(self.ws)
        # No skipTest guard since 2026-09-10. It read "nothing was kept, so
        # there is nothing to corrupt" and was honest while a real kernel
        # decided whether a home existed — but a test that can decline to run
        # is one that stops running. The fake lab always keeps one.
        self.assertTrue(record.home_digest)
        record.home_digest = "b" * 64        # not the bytes that were stored
        record.save(update_fields=["home_digest"])

        with self.assertRaises(hibernation.HibernationError) as caught:
            hibernation.rehydrate(self.ws, user=self.owner)
        self.assertIn("does not match what was stored", str(caught.exception))

    def test_a_workspace_with_nothing_kept_wakes_cleanly(self):
        """The other half, and now the ordinary case.

        Named `..._with_no_environment_...` until 2026-09-10, when the
        environment concept went. What it proves is unchanged and is worth
        keeping: a workspace that kept nothing round-trips without a hook
        having to say so.
        """
        self.give_a_capsule()
        hibernation.hibernate(self.ws, user=self.owner)
        hibernation.rehydrate(self.ws, user=self.owner)
        self.assertFalse(hibernation.is_hibernated(self.ws))


class PartialSnapshotTests(HibernationTestCase):
    """A lab that fails halfway must not strand a workspace."""

    def test_a_failing_snapshot_still_hibernates_and_says_so(self):
        from toto.ambrosia import registry

        # WorkspaceApp is a frozen dataclass, so the hook is swapped by
        # replacing the registry entry rather than by patching an attribute.
        app = registry.for_kind(WorkspaceKind.PYTHON)
        broken = mock.Mock(side_effect=RuntimeError("boom"))
        self.addCleanup(registry._BY_KIND.__setitem__,
                        WorkspaceKind.PYTHON, app)
        registry._BY_KIND[WorkspaceKind.PYTHON] = registry.WorkspaceApp(
            namespace=app.namespace, kind=app.kind,
            extra_context=app.extra_context, extra_urls=app.extra_urls,
            teardown=app.teardown, main_id_for=app.main_id_for,
            settings_fields=app.settings_fields,
            settings_template=app.settings_template,
            room_panels=app.room_panels,
            snapshot=broken, restore=app.restore)

        self.give_a_capsule()
        manifest = hibernation.hibernate(self.ws, user=self.owner)

        self.assertTrue(hibernation.is_hibernated(self.ws))
        self.assertIn("error", manifest)
        # And the compute still went away, which is the part that costs money.
        self.assertEqual(capsule_services.booked().cpu_millicores, 0)


class NoLabKeepsAHomeTests(HibernationTestCase):
    """THE PRODUCT'S ACTUAL STATE since 2026-09-10, pinned so it stays a choice.

    Hybrid hibernation is unreachable: it needs a lab to hand over
    `home_files`, and neither installed lab does. Dracena stopped when its
    kernel went — a Run is a fresh process, so there is no live output area to
    read a $HOME out of — and texlab never did.

    The MACHINERY is deliberately kept: pack, digest, the size cap, the tamper
    refusal and `staged_home` are all still here and still tested above through
    a fake lab. What is gone is a producer. That distinction is the whole point
    of this class — "unused" and "deleted" are different states, and a reader
    finding `permanent_home` on a lease deserves to be told which one this is.

    IF THIS GOES RED because a lab started keeping a home again: that is not a
    bug, it is a decision someone made. Check it was deliberate, then delete
    this class and say so in the manifest tests instead.
    """

    def test_no_installed_lab_contributes_home_files(self):
        from toto.ambrosia import registry

        for kind, app in registry._BY_KIND.items():
            with self.subTest(kind=kind):
                self.assertIsNone(
                    app.snapshot,
                    f"the {app.namespace} lab registered a snapshot hook; if it "
                    f"keeps a home, hybrid hibernation is live again and this "
                    f"class is out of date")

    def test_so_a_permanent_home_capsule_still_hibernates_to_a_manifest(self):
        """The user-visible consequence: reserving `permanent_home` changes
        nothing today. It is recorded in the manifest and keeps no files."""
        self.give_a_capsule(permanent_home=True)
        manifest = hibernation.hibernate(self.ws, user=self.owner)
        self.assertTrue(manifest["permanent_home"])
        self.assertEqual(manifest["depth"], "manifest")
        self.assertEqual(hibernation.staged_home(self.ws), {})

    def test_and_it_wakes_without_complaining(self):
        """Nothing kept means nothing to verify — a wake must not refuse over
        the absence of a home it was never given."""
        self.give_a_capsule(permanent_home=True)
        hibernation.hibernate(self.ws, user=self.owner)
        hibernation.rehydrate(self.ws, user=self.owner)
        self.assertFalse(hibernation.is_hibernated(self.ws))


class ConcurrencyTests(HibernationTestCase):

    def test_two_hibernations_produce_one_record(self):
        self.give_a_capsule()
        hibernation.hibernate(self.ws, user=self.owner)
        hibernation.hibernate(self.ws, user=self.owner)
        self.assertEqual(
            WorkspaceHibernation.objects.filter(workspace=self.ws).count(), 1)

    def test_releasing_twice_is_harmless(self):
        """`release()` is idempotent by design; hibernation leans on that."""
        lease = self.give_a_capsule()
        hibernation.hibernate(self.ws, user=self.owner)
        capsule_services.release(lease=lease, reason="again", actor=self.owner)
        self.assertEqual(ComputeLease.objects.open().count(), 0)

    def test_two_workspaces_hibernate_independently(self):
        other = self.make_workspace(name="Other", kind=WorkspaceKind.PYTHON)
        self.give_a_capsule()
        hibernation.hibernate(self.ws, user=self.owner)
        self.assertTrue(hibernation.is_hibernated(self.ws))
        self.assertFalse(hibernation.is_hibernated(other))


class EndpointTests(HibernationTestCase):

    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def _url(self, name):
        return reverse(f"dracena:{name}", kwargs={"slug": self.ws.slug})

    def test_the_owner_can_put_it_to_sleep_and_wake_it(self):
        self.give_a_capsule()
        response = self.client.post(self._url("workspace_hibernate"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["hibernated"])

        response = self.client.post(self._url("workspace_rehydrate"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["hibernated"])

    def test_a_stranger_cannot(self):
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.post(self._url("workspace_hibernate")).status_code, 404)

    def test_a_refused_wake_answers_409_rather_than_500(self):
        """Nothing is broken and nothing was lost — there is simply a reason.

        Repointed on 2026-09-10 from the package-tree digest (deleted with the
        library installer) to the HOME digest, which is the refusal that
        survives. The endpoint contract is what this test is for: a refusal is
        409 and a sentence, never a traceback.
        """
        self.lab_that_keeps({"home/.bashrc": b"echo hello\n"})
        self.give_a_capsule(permanent_home=True)
        self.client.post(self._url("workspace_hibernate"))

        record = hibernation.record_for(self.ws)
        self.assertTrue(record.home_digest)
        record.home_digest = "b" * 64
        record.save(update_fields=["home_digest"])

        response = self.client.post(self._url("workspace_rehydrate"))
        self.assertEqual(response.status_code, 409)

    def test_neither_endpoint_answers_a_GET(self):
        for name in ("workspace_hibernate", "workspace_rehydrate"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self._url(name)).status_code, 405)
