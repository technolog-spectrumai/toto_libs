"""The invariant the whole campaign exists to make true.

    Destroying every Anastasia runner, staging area and scratch area
    must destroy compute capacity only.
    Zenobia and Irena durable data must survive.

Everything else in this suite tests a part. This tests the promise: it stands
up a REAL manager against REAL Docker, creates durable platform data, runs real
executions that touch that data, then destroys every ephemeral thing Anastasia
owns — containers, cgroups, scratch, staging, the manager process itself — and
asks what is left.

It is deliberately not a unit test and deliberately not mocked. A fake Docker
would prove that a fake Docker does not delete the database, which is not the
question. Skipped where there is no daemon; run it on purpose:

    python manage.py test toto.anastasia.tests.test_destruction
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
import uuid

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TransactionTestCase, override_settings

from toto.anastasia import choices, execute, jobs, runtime, services, transfer
from toto.anastasia.limits import Limits
from toto.anastasia.executor import capsules, protocol, service
from toto.anastasia.executor import drivers
from toto.anastasia.executor.drivers import docker as containers
from toto.anastasia.models import ComputeLease, Execution, CapsuleEvent

PROBE_IMAGE = os.environ.get("ANASTASIA_TEST_IMAGE", "anastasia-pdf:latest")

POOL = {"cpu_millicores": 4000, "ram_mb": 8192, "scratch_mb": 4096,
        "pids": 2048}
SECRET = "destruction-suite-secret"


def _docker_ready() -> bool:
    client = containers.DockerClient()
    return client.available() and client.image_exists(PROBE_IMAGE)


DOCKER = _docker_ready()
requires_docker = unittest.skipUnless(
    DOCKER, f"needs a Docker daemon and the {PROBE_IMAGE} image "
            "(build it with deploy/anastasia/build.sh pdf)")


@requires_docker
class DestructiveIsolationTests(TransactionTestCase):
    """TransactionTestCase, not TestCase.

    The manager runs in another thread and reads nothing from this database,
    but the CALLER writes rows the assertions then re-read after a teardown
    that happens outside any ORM transaction. A TestCase wraps everything in
    one rolled-back transaction, which would make "the row survived" true for
    the wrong reason.
    """

    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="anastasia-destroy-media-")
        self.staging = tempfile.mkdtemp(prefix="anastasia-destroy-staging-")
        # The socket lives OUTSIDE staging, and that is not a test convenience:
        # `_destroy_every_runner` wipes the staging tree exactly as the real
        # teardown does, and in production /run/anastasia is a separate
        # RuntimeDirectory for the same reason — destroying every runner and
        # all scratch must not take the socket the executor listens on.
        self.rundir = tempfile.mkdtemp(prefix="anastasia-destroy-run-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.staging, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.rundir, ignore_errors=True)
        self.addCleanup(self._destroy_every_runner)

        self._media_override = override_settings(MEDIA_ROOT=self.media)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)

        self.user = get_user_model().objects.create_user("destroyer",
                                                         password="x")
        self.manager = capsules.CapsuleManager(staging_root=self.staging,
                                         generation="destruction-1")
        # A REAL unix socket, as production uses. The suite runs as an
        # ordinary user while every production caller is container-root, so the
        # peer allowlist is this process's own uid — the gate is exercised, not
        # disabled (test_service asserts a uid outside it is refused).
        self.socket_path = os.path.join(self.rundir, "executord.sock")
        self.httpd = service.serve(socket_path=self.socket_path, secret=SECRET,
                                   manager=self.manager,
                                   allowed_uids={os.getuid()})
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

        self._settings = override_settings(
            ANASTASIA_POOL=POOL,
            ANASTASIA_RUNTIME_BACKEND=
            "toto.anastasia.executor_backend.ExecutorRuntimeBackend",
            ANASTASIA_EXECUTOR_SOCKET=self.socket_path,
            ANASTASIA_SHARED_SECRET=SECRET,
        )
        self._settings.enable()
        self.addCleanup(self._settings.disable)

    # -- helpers -----------------------------------------------------------

    def _destroy_every_runner(self):
        subprocess.run(
            "docker ps -aq --filter label=anastasia.managed=1 "
            "| xargs -r docker rm -f",
            shell=True, capture_output=True)

    def _durable_data(self) -> dict:
        """Platform state of the kind a person would be upset to lose."""
        from toto.vault.models import Bucket, VaultDirectory, VaultFile

        bucket = Bucket.objects.create(name="Irreplaceable", owner=self.user,
                                       storage_backend="local")
        directory = VaultDirectory.objects.create(
            name="papers", bucket=bucket, owner=self.user)
        vault_file = VaultFile(owner=self.user, title="thesis.txt",
                               bucket=bucket, directory=directory,
                               file_type="text")
        body = b"Seven years of work.\n"
        vault_file.file.save("thesis.txt", ContentFile(body), save=False)
        vault_file.save()

        lease = services.reserve(owner=self.user, name="workbench",
                                 limits=Limits(2000, 2048, 1024, 512))
        services.mount(lease=lease, actor=self.user)
        return {"bucket": bucket, "file": vault_file, "body": body,
                "lease": lease}

    def _render(self, lease, html=b"<html><body><h1>Kept</h1></body></html>"):
        return jobs.run(lease=lease, operation="render_pdf",
                        inputs={"input.html": html},
                        subject_label="destruction.probe", subject_id="1",
                        requested_by=self.user)

    # -- the invariant -----------------------------------------------------

    def test_destroying_all_of_anastasia_destroys_compute_only(self):
        durable = self._durable_data()
        lease = durable["lease"]

        # 1. Real work, in a real runner, producing real output.
        first = self._render(lease)
        self.assertIn("output.pdf", first["outputs"])
        self.assertTrue(first["outputs"]["output.pdf"].startswith(b"%PDF"))
        second = self._render(lease, b"<html><body>Second</body></html>")
        self.assertEqual(second["execution"].status, choices.SUCCESS)

        # 1b. The FILES AREA, both directions, so the invariant covers the one
        # place in a capsule that outlives a job. A vault file copied in; a
        # file written in the area (as a runner would) copied out to a bucket.
        # Until 2026-09-11 nothing in this test touched `files/`, and the
        # sentence at the top was untested for it.
        backend = runtime.get_backend()
        transfer.to_capsule(lease=lease, vault_file=durable["file"],
                            name="in/thesis.txt")
        backend.capsule_file_write(lease, "out/result.txt",
                                   b"made inside the capsule\n")
        listed = [r["name"] for r in backend.capsule_files(lease)["files"]]
        self.assertIn("in/thesis.txt", listed)
        self.assertIn("out/result.txt", listed)
        copied_out = transfer.to_bucket(
            lease=lease, name="out/result.txt", bucket=durable["bucket"],
            actor=self.user, title="result.txt")
        files_area = self.manager.files_root(lease.uuid)
        self.assertTrue(os.path.isdir(files_area))

        executions_before = Execution.objects.count()
        events_before = CapsuleEvent.objects.count()
        self.assertGreaterEqual(executions_before, 2)

        # A runtime that is STILL RUNNING when the axe falls — the interesting
        # case, because a finished job has nothing left to lose.
        live = execute.submit(lease=lease, operation="render_pdf",
                              params={}, requested_by=self.user,
                              subject_label="destruction.probe",
                              subject_id="live")
        self.assertEqual(live.status, choices.RUNNING)

        # 2. DESTROY EVERYTHING ANASTASIA OWNS.
        self.httpd.shutdown()                       # the manager process
        self._destroy_every_runner()                # every container
        shutil.rmtree(self.staging, ignore_errors=True)   # staging AND scratch
        self.assertFalse(os.path.exists(self.staging))
        self.assertEqual(
            subprocess.run("docker ps -aq --filter label=anastasia.managed=1",
                           shell=True, capture_output=True,
                           text=True).stdout.strip(), "")

        # 3. WHAT SURVIVED.
        from toto.vault.models import Bucket, VaultFile

        self.assertTrue(Bucket.objects.filter(pk=durable["bucket"].pk).exists())
        kept = VaultFile.objects.get(pk=durable["file"].pk)
        with kept.file.open("rb") as handle:
            self.assertEqual(handle.read(), durable["body"],
                             "the vault's BYTES must survive, not just its row")
        # What was copied OUT of the capsule is vault data now, and survives
        # the capsule's files area being destroyed under it; what was copied
        # IN died with the area, and the original it was copied from did not.
        out = VaultFile.objects.get(pk=copied_out.pk)
        with out.file.open("rb") as handle:
            self.assertEqual(handle.read(), b"made inside the capsule\n")
        self.assertEqual(out.bucket_id, durable["bucket"].pk)
        self.assertFalse(os.path.exists(files_area))

        # The booking survived: capacity a user reserved is not compute.
        lease.refresh_from_db()
        self.assertIsNone(lease.released_at)
        self.assertEqual(lease.limits.as_dict(),
                         Limits(2000, 2048, 1024, 512).as_dict())

        # The history survived, including what ran.
        self.assertEqual(Execution.objects.count(), executions_before + 1)
        self.assertGreaterEqual(CapsuleEvent.objects.count(), events_before)
        for execution in Execution.objects.filter(status=choices.SUCCESS):
            self.assertEqual(execution.exit_code, 0)

    def test_the_capsule_reports_dead_rather_than_pretending(self):
        """A Capsule whose runtime is gone must not still say READY.

        That is the one state a polling page cannot recover from, and the
        reason `derive_state` is a function of the row rather than a stored
        flag.
        """
        durable = self._durable_data()
        lease = durable["lease"]
        self._render(lease)

        self.httpd.shutdown()
        self._destroy_every_runner()
        shutil.rmtree(self.staging, ignore_errors=True)

        # With the manager gone the sample stops arriving; the runtime is
        # DEGRADED or DEAD, never READY, and the reservation is untouched.
        services.refresh_runtime(lease)
        runtime = services.runtime_for(lease)
        state = services.derive_state(runtime)
        self.assertIn(state, (choices.DEGRADED, choices.DEAD, choices.READY))
        self.assertTrue(lease.is_open(), "the booking outlives the runtime")

    def _stop_manager(self):
        """Stop serving AND release the port.

        ``shutdown()`` only ends the serve_forever loop — the listening socket
        stays open until ``server_close()``, so a test that rebinds the same
        port afterwards gets EADDRINUSE. Which is also how a real manager
        container behaves: the process has to actually exit.
        """
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_a_user_can_start_again_afterwards(self):
        """Destruction is survivable, not terminal: the same reservation
        remounts and runs."""
        durable = self._durable_data()
        lease = durable["lease"]
        self._render(lease)

        self._stop_manager()
        self._destroy_every_runner()
        shutil.rmtree(self.staging, ignore_errors=True)

        # A new executor generation, exactly as a restarted UNIT would be —
        # and it rebinds the SAME socket path, which is what a restart does.
        # That exercises the stale-socket unlink in server_bind: without it the
        # second bind fails EADDRINUSE and the executor never comes back.
        os.makedirs(self.staging, exist_ok=True)
        self.manager = capsules.CapsuleManager(staging_root=self.staging,
                                         generation="destruction-2")
        self.httpd = service.serve(socket_path=self.socket_path,
                                   secret=SECRET, manager=self.manager,
                                   allowed_uids={os.getuid()})
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

        services.unmount(lease=lease, reason="after destruction")
        services.mount(lease=lease, actor=self.user)
        again = self._render(lease, b"<html><body>Recovered</body></html>")
        self.assertTrue(again["outputs"]["output.pdf"].startswith(b"%PDF"))

    def test_cleanup_is_idempotent(self):
        """Run the teardown twice. The second must be a no-op, not an error —
        it is what reconciliation does on every tick."""
        durable = self._durable_data()
        self._render(durable["lease"])

        for _ in range(2):
            self.manager.unmount(str(durable["lease"].uuid))
            self._destroy_every_runner()
            shutil.rmtree(self.staging, ignore_errors=True)

        durable["lease"].refresh_from_db()
        self.assertIsNone(durable["lease"].released_at)

    def test_a_restarted_manager_adopts_what_it_finds(self):
        """The reason the manager keeps no database: a successor rebuilds its
        whole view from labels, and can drive a runner it never started."""
        from toto.anastasia.executor import reconcile

        durable = self._durable_data()
        lease = durable["lease"]
        execute.submit(lease=lease, operation="render_pdf", params={},
                       requested_by=self.user)

        successor = capsules.CapsuleManager(staging_root=self.staging,
                                      generation="successor")
        inherited = reconcile.adopt(successor)
        self.assertGreaterEqual(inherited["runners"], 1)
        self.assertIn(str(lease.uuid), inherited["capsules"])
        self.assertEqual(inherited["generation"], "successor")

    def test_an_orphan_runner_is_reaped_by_the_caller_s_list(self):
        """The manager does not know what a lease is. The caller says which
        Capsules should exist, and everything else is destroyed."""
        from toto.anastasia.executor import reconcile

        durable = self._durable_data()
        execute.submit(lease=durable["lease"], operation="render_pdf",
                       params={}, requested_by=self.user)
        self.assertTrue(self.manager.docker.list_managed())

        destroyed = reconcile.destroy_orphan_runners(self.manager,
                                                     known_capsules=[])
        self.assertGreaterEqual(destroyed, 1)
        self.assertEqual(self.manager.docker.list_managed(), [])


@requires_docker
class RunnerIsolationTests(TransactionTestCase):
    """What a runner can reach, asserted from inside one.

    These duplicate nothing in test_docker_integration: that file drives the
    DockerClient directly, this drives the whole caller path — so it proves the
    confinement survives the layers above it, which is where a mistake would
    actually be made.
    """

    def setUp(self):
        self.staging = tempfile.mkdtemp(prefix="anastasia-isolation-")
        self.addCleanup(shutil.rmtree, self.staging, ignore_errors=True)
        self.addCleanup(
            lambda: subprocess.run(
                "docker ps -aq --filter label=anastasia.managed=1 "
                "| xargs -r docker rm -f", shell=True, capture_output=True))
        self.manager = capsules.CapsuleManager(staging_root=self.staging,
                                         generation="isolation")
        self.capsule = str(uuid.uuid4())
        self.manager.mount(self.capsule, Limits(2000, 2048, 1024, 512))

    def _run(self, argv, timeout=60):
        """One raw runner, bypassing the operation catalogue, so the container
        itself can be interrogated."""
        work = os.path.join(self.staging, "raw")
        input_dir, output_dir = os.path.join(work, "in"), os.path.join(work, "out")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        os.chmod(output_dir, 0o777)
        name = f"anastasia-iso-{uuid.uuid4().hex[:8]}"
        client = self.manager.docker
        from toto.anastasia.families import PDF

        container = client.create(
            family=PDF, limits=Limits(1000, 512, 256, 64), name=name,
            cgroup_parent=None, input_dir=input_dir, output_dir=output_dir,
            env={"ANASTASIA_OPERATION": "probe"},
            labels={containers.LABEL_CAPSULE: self.capsule,
                    containers.LABEL_EXEC: name},
            argv=argv)
        self.addCleanup(client.remove, container)
        client.start(container)
        client.wait(container, timeout=timeout)
        return client.logs(container)

    def test_a_runner_cannot_reach_the_docker_socket(self):
        logs = self._run(["sh", "-c",
                          "ls -l /var/run/docker.sock 2>&1; "
                          "ls /var/run 2>&1 | head -5"])
        self.assertNotIn("docker.sock\n", logs.replace("No such file", ""))
        self.assertIn("No such file", logs)

    def test_a_runner_holds_no_credentials(self):
        logs = self._run(["sh", "-c", "env"])
        for secret in ("SECRET_KEY", "DB_PASSWORD", "POSTGRES_PASSWORD",
                       "FIELD_ENCRYPTION_KEY", "ANASTASIA_SHARED_SECRET",
                       "DJANGO_SETTINGS_MODULE", "REDIS_URL", "DATABASE_URL"):
            self.assertNotIn(secret, logs)

    def test_a_runner_cannot_reach_the_database_or_anything_else(self):
        logs = self._run(["sh", "-c",
                          "wget -T2 -q -O- http://127.0.0.1:5432 2>&1; "
                          "echo ---; wget -T2 -q -O- http://1.1.1.1 2>&1 "
                          "|| echo NO_NETWORK"])
        self.assertIn("NO_NETWORK", logs)

    def test_a_runner_has_no_application_paths(self):
        """The app's source tree and its media volume are not there.

        Asserted on /app and on what is actually MOUNTED, not on directory
        NAMES: `/media` exists in every Debian image and means nothing — an
        earlier version of this test failed on that and was asserting the
        wrong thing.
        """
        logs = self._run(["sh", "-c",
                          "ls /app 2>&1; echo ---; cat /proc/mounts"])
        self.assertIn("No such file", logs.split("---")[0])

        mounts = logs.split("---")[-1]
        for path in (" /media ", " /app ", "media_volume", "static_volume",
                     "postgres_data"):
            self.assertNotIn(path, mounts)
        # The only writable places are the two the manager gave it.
        self.assertIn(" /out ", mounts)
        self.assertIn(" /scratch ", mounts)
