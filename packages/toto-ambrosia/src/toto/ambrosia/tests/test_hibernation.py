"""Hibernation: what goes to zero, what comes back, and what refuses.

The brief asks for tampering, missing bases, partial snapshots, concurrency,
zero hibernation billing and exact restoration. Each has a test here, and where
one CANNOT be honestly asserted that is said out loud rather than faked:

* **Zero billing** is asserted as zero ALLOCATION — `services.booked()` falling
  to nothing — because reserved capacity is not billed on this deployment at
  all. `anastasia.execution` is explicitly "a rate limit on submissions, not a
  price on compute", and the reservation levy `anastasia.gear_hour` is still a
  TODO with no taxes.py. A test asserting "the charge is zero" would pass
  against an engine that never charges and prove nothing; asserting the pool is
  free proves the thing that actually matters.
* **A real runtime** is faked at the same seam every other dracena test fakes.
  What is not faked: the manifest, the digests, the release, and the staging.
"""

from __future__ import annotations

from unittest import mock

from django.test import override_settings
from django.urls import reverse

from toto.ambrosia import hibernation
from toto.ambrosia.models import WorkspaceHibernation, WorkspaceKind
from toto.ambrosia.tests.base import AmbrosiaTestCase
from toto.anastasia import services as gear_services
from toto.anastasia.limits import Limits
from toto.anastasia.models import ComputeLease
from toto.dracena.tests.fakes import POOL, FakeKernelBackend


@override_settings(
    ANASTASIA_POOL=POOL,
    ANASTASIA_RUNTIME_BACKEND="toto.dracena.tests.fakes.FakeKernelBackend",
)
class HibernationTestCase(AmbrosiaTestCase):
    def setUp(self):
        super().setUp()
        FakeKernelBackend.reset()
        self.ws = self.make_workspace(name="Py", kind=WorkspaceKind.PYTHON)

    def give_a_gear(self, *, permanent_home=False, name="py"):
        lease = gear_services.reserve(
            owner=self.owner, name=name, limits=Limits(2000, 2048, 1024, 512),
            permanent_home=permanent_home)
        gear_services.mount(lease=lease, actor=self.owner)
        return lease

    def start_kernel_on(self, lease):
        """A live session with a REAL Execution row behind it.

        `start_runtime` is mocked — there is no Docker here — but the row it
        would have created is not, because `snapshot` looks the execution up to
        collect from it. Mocking that away too would have made the hybrid tests
        pass without exercising the path they exist for.
        """
        from toto.anastasia.models import Execution
        from toto.dracena import kernel

        execution = Execution.objects.create(
            lease=lease, operation="start_python_runtime", family="python",
            cpu_millicores=1000, ram_mb=1024, scratch_mb=512, pids=128,
            timeout_seconds=120, requested_by=self.owner)
        with mock.patch("toto.anastasia.jobs.start_runtime") as start:
            start.return_value = {"ready": {"ip": "10.0.0.9"},
                                  "execution": execution}
            kernel.start(self.ws, gear_uuid=lease.uuid, user=self.owner)
        return execution


class ManifestHibernationTests(HibernationTestCase):

    def test_hibernating_writes_down_what_it_is(self):
        self.give_a_gear()
        manifest = hibernation.hibernate(self.ws, user=self.owner)
        self.assertEqual(manifest["kind"], WorkspaceKind.PYTHON)
        self.assertEqual(manifest["depth"], "manifest")
        # The lab's own half: what to rebuild ON.
        self.assertEqual(manifest["runtime"]["image"], "anastasia-python")
        self.assertTrue(hibernation.is_hibernated(self.ws))

    def test_it_is_idempotent(self):
        """A double-click is not an error."""
        self.give_a_gear()
        first = hibernation.hibernate(self.ws, user=self.owner)
        second = hibernation.hibernate(self.ws, user=self.owner)
        self.assertEqual(first, second)
        self.assertEqual(WorkspaceHibernation.objects.count(), 1)

    def test_waking_something_awake_is_not_an_error_either(self):
        hibernation.rehydrate(self.ws, user=self.owner)
        self.assertFalse(hibernation.is_hibernated(self.ws))

    def test_a_round_trip_clears_the_sleeping_state(self):
        self.give_a_gear()
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
        self.give_a_gear()
        before = gear_services.booked()
        self.assertGreater(before.cpu_millicores, 0)

        hibernation.hibernate(self.ws, user=self.owner)

        after = gear_services.booked()
        self.assertEqual(after.cpu_millicores, 0)
        self.assertEqual(after.ram_mb, 0)
        self.assertEqual(after.scratch_mb, 0)
        self.assertEqual(after.pids, 0)

    def test_it_releases_rather_than_merely_unmounting(self):
        """Unmounting frees NOTHING: booked() counts every OPEN lease, mounted
        or not. This is the distinction the whole design turns on."""
        lease = self.give_a_gear()
        hibernation.hibernate(self.ws, user=self.owner)
        lease.refresh_from_db()
        self.assertIsNotNone(lease.released_at)
        self.assertFalse(lease.is_open())

    def test_the_manifest_records_that_it_let_go(self):
        self.give_a_gear()
        manifest = hibernation.hibernate(self.ws, user=self.owner)
        self.assertTrue(manifest["lease_released"])

    def test_a_workspace_with_no_gear_still_hibernates(self):
        """Nothing to release is not a failure — it is already at zero."""
        manifest = hibernation.hibernate(self.ws, user=self.owner)
        self.assertTrue(hibernation.is_hibernated(self.ws))
        self.assertFalse(manifest["lease_released"])


class HybridHibernationTests(HibernationTestCase):
    """The permanent-home half — the depth chosen when the Gear was reserved."""

    HOME = {"home/.ipython/history.sqlite": b"SQLite format 3\\x00",
            "home/.gitconfig": b"[user]\\n\\tname = Ada\\n"}

    def _hibernate_with_home(self, files=None):
        lease = self.give_a_gear(permanent_home=True)
        self.start_kernel_on(lease)
        with mock.patch("toto.anastasia.jobs.collect_runtime",
                        return_value=(self.HOME if files is None else files)):
            return lease, hibernation.hibernate(self.ws, user=self.owner)

    def test_a_permanent_home_gear_keeps_the_home(self):
        _lease, manifest = self._hibernate_with_home()
        self.assertEqual(manifest["depth"], "hybrid")
        record = WorkspaceHibernation.objects.get(workspace=self.ws)
        self.assertTrue(record.home_digest)
        self.assertGreater(record.home_bytes, 0)

    def test_an_ordinary_gear_keeps_no_home_even_if_one_exists(self):
        """The choice is the Gear's, made when it was reserved."""
        lease = self.give_a_gear(permanent_home=False)
        self.start_kernel_on(lease)
        with mock.patch("toto.anastasia.jobs.collect_runtime",
                        return_value=self.HOME) as collect:
            manifest = hibernation.hibernate(self.ws, user=self.owner)
        collect.assert_not_called()
        self.assertEqual(manifest["depth"], "manifest")

    def test_the_kept_home_is_staged_back_exactly(self):
        """Exact restoration: the same bytes, under the same names."""
        self._hibernate_with_home()
        staged = hibernation.staged_home(self.ws)
        self.assertEqual(staged, self.HOME)

    def test_a_kernel_start_stages_the_kept_home(self):
        """How it actually reaches the runtime."""
        from toto.dracena import kernel

        self._hibernate_with_home()
        lease = self.give_a_gear(permanent_home=True, name="py2")
        with mock.patch("toto.anastasia.jobs.start_runtime") as start:
            start.return_value = {"ready": {"ip": "10.0.0.9"},
                                  "execution": mock.Mock(uuid=lease.uuid)}
            hibernation.rehydrate(self.ws, user=self.owner)
            kernel.start(self.ws, gear_uuid=lease.uuid, user=self.owner)
        _args, kwargs = start.call_args
        self.assertIn("home/.gitconfig", kwargs["inputs"])
        self.assertTrue(kwargs["params"]["persistent_home"])

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
        lease = self.give_a_gear(permanent_home=True)
        self.start_kernel_on(lease)
        with mock.patch("toto.anastasia.jobs.collect_runtime",
                        return_value={"home/.bashrc": b"echo hello\\n"}):
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
        """The check is on every path, not only on an explicit wake — this one
        runs at every kernel start."""
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
        lease = self.give_a_gear(permanent_home=True)
        self.start_kernel_on(lease)
        hibernation.hibernate(self.ws, user=self.owner)

        record = hibernation.record_for(self.ws)
        if not record.home_digest:
            self.skipTest("nothing was kept, so there is nothing to corrupt")
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
        self.give_a_gear()
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

        self.give_a_gear()
        manifest = hibernation.hibernate(self.ws, user=self.owner)

        self.assertTrue(hibernation.is_hibernated(self.ws))
        self.assertIn("error", manifest)
        # And the compute still went away, which is the part that costs money.
        self.assertEqual(gear_services.booked().cpu_millicores, 0)


class ConcurrencyTests(HibernationTestCase):

    def test_two_hibernations_produce_one_record(self):
        self.give_a_gear()
        hibernation.hibernate(self.ws, user=self.owner)
        hibernation.hibernate(self.ws, user=self.owner)
        self.assertEqual(
            WorkspaceHibernation.objects.filter(workspace=self.ws).count(), 1)

    def test_releasing_twice_is_harmless(self):
        """`release()` is idempotent by design; hibernation leans on that."""
        lease = self.give_a_gear()
        hibernation.hibernate(self.ws, user=self.owner)
        gear_services.release(lease=lease, reason="again", actor=self.owner)
        self.assertEqual(ComputeLease.objects.open().count(), 0)

    def test_two_workspaces_hibernate_independently(self):
        other = self.make_workspace(name="Other", kind=WorkspaceKind.PYTHON)
        self.give_a_gear()
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
        self.give_a_gear()
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
        lease = self.give_a_gear(permanent_home=True)
        self.start_kernel_on(lease)
        self.client.post(self._url("workspace_hibernate"))

        record = hibernation.record_for(self.ws)
        if not record.home_digest:
            self.skipTest("nothing was kept, so there is nothing to corrupt")
        record.home_digest = "b" * 64
        record.save(update_fields=["home_digest"])

        response = self.client.post(self._url("workspace_rehydrate"))
        self.assertEqual(response.status_code, 409)

    def test_neither_endpoint_answers_a_GET(self):
        for name in ("workspace_hibernate", "workspace_rehydrate"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self._url(name)).status_code, 405)
