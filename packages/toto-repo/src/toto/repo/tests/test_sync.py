"""Sync engine: export layout, import fold-back, idempotency, nesting guard."""

from django.core.files.uploadedfile import SimpleUploadedFile

from toto.repo import services, sync
from toto.repo.models import GitRepo, GitRepoFile
from toto.vault.models import VaultDirectory, VaultFile

from .base import RepoTestCase


class ArtifactExclusionTests(RepoTestCase):
    """Generated output does not belong in a repository.

    Every surface that gets git also generates files beside the sources: a
    LaTeX workspace files its PDF and logs into ``build/``, and the document
    editor saves pdf/html renditions next to the .xml they came from. Both
    change on every compile and neither is a source, so committing them turns
    every history into noise and every merge into a conflict over a binary.
    """

    def test_the_build_directory_is_not_exported(self):
        build = VaultDirectory.objects.create(
            bucket=self.bucket, owner=self.user, name="build", parent=self.root)
        self._file("paper.pdf", b"%PDF-1.4 output\n", build,
                   key="build-paper-pdf", file_type="pdf")
        self._file("paper.log", b"This is pdfTeX\n", build, key="build-paper-log")

        result = sync.export_worktree(
            GitRepo.objects.create(directory=self.root, owner=self.user))

        self.assertNotIn("build/paper.pdf", result["written"])
        self.assertNotIn("build/paper.log", result["written"])
        self.assertEqual(sorted(result["written"]), ["notes.txt", "sub/deep.txt"])

    def test_a_generated_sibling_is_not_exported(self):
        # texlab writes .aux/.log beside a source when it is not using build/.
        # Explicit keys because both slug to "paper" and VaultFile.save()
        # refuses a duplicate — the real writers call _unique_file_key.
        self._file("paper.aux", b"\\relax\n", self.root, key="paper-aux")
        self._file("paper.tex", b"\\documentclass{article}\n", self.root, key="paper-tex")

        result = sync.export_worktree(
            GitRepo.objects.create(directory=self.root, owner=self.user))

        self.assertIn("paper.tex", result["written"])
        self.assertNotIn("paper.aux", result["written"])

    def test_the_source_a_rendition_came_from_still_travels(self):
        # Only the DERIVED file is skipped. Losing the document itself would
        # be a far worse bug than versioning its pdf.
        #
        # The source is an `html` file, not the old `document`/report.xml: a
        # written document IS ordinary html since CTML was retired on
        # 2026-08-29, and this fixture kept asserting a spelling the platform
        # had deleted from VaultFile.FILE_TYPE_CHOICES.
        self._file("report.html", b"<h1>R</h1>\n", self.root,
                   key="report-html", file_type="html")
        self._file("report.pdf", b"%PDF-1.4\n", self.root,
                   key="report-pdf", file_type="pdf")

        result = sync.export_worktree(
            GitRepo.objects.create(directory=self.root, owner=self.user))

        self.assertIn("report.html", result["written"])
        self.assertNotIn("report.pdf", result["written"])

    def test_a_document_is_never_its_own_rendition(self):
        """The regression that made every html document vanish from git.

        `html` is on both sides of the rendition test — a document is html,
        and "Save as HTML" from a deck is html too. While the index held bare
        stems, a lone report.html matched the key it had itself contributed,
        was skipped, never entered `seen_paths`, and was then UNLINKED by the
        orphan sweep and deleted from git on the next commit. Documents are
        the thing this repo exists to version.
        """
        self._file("solo.html", b"<h1>S</h1>\n", self.root,
                   key="solo-html", file_type="html")

        result = sync.export_worktree(
            GitRepo.objects.create(directory=self.root, owner=self.user))

        self.assertIn("solo.html", result["written"])

    def test_two_documents_sharing_a_title_both_travel(self):
        """Vault titles are not unique, so a same-stem sibling must not be
        mistaken for a source. Keying the index by pk instead of by TYPE
        would drop both of these."""
        self._file("dup.html", b"<h1>A</h1>\n", self.root,
                   key="dup-a", file_type="html")
        self._file("dup.html", b"<h1>B</h1>\n", self.root,
                   key="dup-b", file_type="html")

        result = sync.export_worktree(
            GitRepo.objects.create(directory=self.root, owner=self.user))

        self.assertEqual(
            len([p for p in result["written"] if p.startswith("dup")]), 2,
            result["written"])

    def test_html_saved_from_a_deck_is_still_a_rendition(self):
        """The rule survives from the other side: a DIFFERENT source type
        beside it still makes an html file derived."""
        self._file("talk.pxml", b"<deck/>\n", self.root,
                   key="talk-pxml", file_type="pxml")
        self._file("talk.html", b"<h1>T</h1>\n", self.root,
                   key="talk-html", file_type="html")

        result = sync.export_worktree(
            GitRepo.objects.create(directory=self.root, owner=self.user))

        self.assertIn("talk.pxml", result["written"])
        self.assertNotIn("talk.html", result["written"])


class ExportTests(RepoTestCase):
    def test_export_layout_and_mapping(self):
        repo = GitRepo.objects.create(directory=self.root, owner=self.user)
        result = sync.export_worktree(repo)
        self.assertEqual(sorted(result["written"]), ["notes.txt", "sub/deep.txt"])
        self.assertEqual((repo.worktree / "notes.txt").read_bytes(), b"hello notes\n")
        self.assertEqual((repo.worktree / "sub" / "deep.txt").read_bytes(), b"deep content\n")
        # encrypted file is invisible to git
        self.assertFalse((repo.worktree / "secret.txt").exists())
        self.assertEqual(
            sorted(m.relpath for m in repo.files.all()),
            ["notes.txt", "sub/deep.txt"],
        )

    def test_export_is_idempotent(self):
        repo = GitRepo.objects.create(directory=self.root, owner=self.user)
        sync.export_worktree(repo)
        second = sync.export_worktree(repo)
        self.assertEqual(second, {"written": [], "removed": []})

    def test_title_collision_dedup_is_stable(self):
        twin = self._file("notes.txt", b"other notes\n", self.root, key="notes-twin")
        repo = GitRepo.objects.create(directory=self.root, owner=self.user)
        sync.export_worktree(repo)
        paths = sorted(m.relpath for m in repo.files.all())
        self.assertIn("notes.txt", paths)
        self.assertIn("notes-1.txt", paths)
        # stable across re-export (pk-ordered walk)
        sync.export_worktree(repo)
        self.assertEqual(sorted(m.relpath for m in repo.files.all()), paths)
        self.assertEqual(
            GitRepoFile.objects.get(vault_file=twin).relpath, "notes-1.txt"
        )

    def test_vault_deletion_removes_worktree_file(self):
        repo = GitRepo.objects.create(directory=self.root, owner=self.user)
        sync.export_worktree(repo)
        self.f_deep.delete()
        result = sync.export_worktree(repo)
        self.assertEqual(result["removed"], ["sub/deep.txt"])
        self.assertFalse((repo.worktree / "sub").exists())  # empty dir pruned

    def test_vault_content_change_rewrites(self):
        repo = GitRepo.objects.create(directory=self.root, owner=self.user)
        sync.export_worktree(repo)
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"edited\n")
        result = sync.export_worktree(repo)
        self.assertEqual(result["written"], ["notes.txt"])
        self.assertEqual((repo.worktree / "notes.txt").read_bytes(), b"edited\n")


class ImportTests(RepoTestCase):
    def _repo(self):
        return self.make_repo()

    def test_import_new_file_and_directory(self):
        repo = self._repo()
        new_dir = repo.worktree / "docs" / "inner"
        new_dir.mkdir(parents=True)
        (new_dir / "readme.md").write_bytes(b"# hi\n")
        summary = sync.import_worktree(repo)
        self.assertEqual(summary["created"], ["docs/inner/readme.md"])

        docs = VaultDirectory.objects.get(name="docs", parent=self.root)
        inner = VaultDirectory.objects.get(name="inner", parent=docs)
        vf = VaultFile.objects.get(title="readme.md")
        self.assertEqual(vf.directory, inner)
        # `.md` types as 'markdown', not 'text', since vault migration 0025 —
        # the import door reads `_EXT_MAP` like every other ingest door, which
        # is the property this line is really asserting.
        self.assertEqual(vf.file_type, "markdown")
        self.assertEqual(vf.file.read(), b"# hi\n")
        self.assertTrue(vf.content_hash)

    def test_import_update_recomputes_hash(self):
        repo = self._repo()
        old_hash = VaultFile.objects.get(pk=self.f_notes.pk).content_hash
        (repo.worktree / "notes.txt").write_bytes(b"changed upstream\n")
        summary = sync.import_worktree(repo)
        self.assertEqual(summary["updated"], ["notes.txt"])
        vf = VaultFile.objects.get(pk=self.f_notes.pk)
        self.assertEqual(vf.file.read(), b"changed upstream\n")
        self.assertNotEqual(vf.content_hash, old_hash)

    def test_import_deletion_deletes_vault_file(self):
        repo = self._repo()
        (repo.worktree / "sub" / "deep.txt").unlink()
        summary = sync.import_worktree(repo)
        self.assertEqual(summary["deleted"], ["sub/deep.txt"])
        self.assertFalse(VaultFile.objects.filter(pk=self.f_deep.pk).exists())

    def test_import_noop(self):
        repo = self._repo()
        summary = sync.import_worktree(repo)
        self.assertEqual(summary, {"created": [], "updated": [], "deleted": [],
                                   "refused": []})

    def test_import_leaves_office_files_out_of_the_vault(self):
        """No Microsoft Office file enters through a pull (2026-09-30), by
        name or by content; an OpenDocument file does."""
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("word/document.xml", "<x/>")
        repo = self._repo()
        (repo.worktree / "minutes.docx").write_bytes(buf.getvalue())
        (repo.worktree / "renamed.zip").write_bytes(buf.getvalue())
        (repo.worktree / "old.xls").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
        (repo.worktree / "letter.odt").write_bytes(b"PK\x03\x04 not office")
        summary = sync.import_worktree(repo)
        self.assertEqual(sorted(summary["refused"]), ["minutes.docx", "old.xls", "renamed.zip"])
        self.assertEqual(summary["created"], ["letter.odt"])
        self.assertFalse(VaultFile.objects.filter(
            title__in=["minutes.docx", "renamed.zip", "old.xls"]).exists())


class TrashTests(RepoTestCase):
    """The vault's trash (2026-10-01): a pulled deletion trashes, and a file
    already in the trash has left the repo."""

    def test_a_pulled_deletion_moves_the_file_to_the_trash(self):
        repo = self.make_repo()
        (repo.worktree / "sub" / "deep.txt").unlink()
        summary = sync.import_worktree(repo)
        self.assertEqual(summary["deleted"], ["sub/deep.txt"])
        row = VaultFile.all_objects.get(pk=self.f_deep.pk)
        self.assertIsNotNone(row.trashed_at)
        self.assertEqual(row.file.read(), b"deep content\n")
        self.assertFalse(GitRepoFile.objects.filter(vault_file_id=self.f_deep.pk).exists())

    def test_a_pull_never_writes_into_a_trashed_file(self):
        repo = self.make_repo()
        self.f_notes.trash(self.user)
        (repo.worktree / "notes.txt").write_bytes(b"changed upstream\n")
        summary = sync.import_worktree(repo)
        self.assertEqual(summary["created"], ["notes.txt"])
        trashed = VaultFile.all_objects.get(pk=self.f_notes.pk)
        self.assertEqual(trashed.file.read(), b"hello notes\n")
        fresh = GitRepoFile.objects.get(repo=repo, relpath="notes.txt").vault_file
        self.assertNotEqual(fresh.pk, self.f_notes.pk)
        self.assertIsNone(fresh.trashed_at)

    def test_a_pull_never_deletes_a_file_waiting_in_the_trash(self):
        repo = self.make_repo()
        self.f_deep.trash(self.user)
        (repo.worktree / "sub" / "deep.txt").unlink()
        summary = sync.import_worktree(repo)
        self.assertEqual(summary["deleted"], [])
        self.assertTrue(VaultFile.all_objects.filter(pk=self.f_deep.pk).exists())


class GuardTests(RepoTestCase):
    def test_nesting_guard_descendant(self):
        services.init_repo(self.sub, self.user)
        with self.assertRaises(services.RepoError):
            services.init_repo(self.root, self.user)

    def test_nesting_guard_ancestor(self):
        services.init_repo(self.root, self.user)
        with self.assertRaises(services.RepoError):
            services.init_repo(self.sub, self.user)

    def test_repo_lock_blocks(self):
        repo = self.make_repo()
        with sync.repo_lock(repo):
            with self.assertRaises(sync.RepoBusy):
                with sync.repo_lock(repo):
                    pass


class ServiceFlowTests(RepoTestCase):
    def test_init_creates_initial_commit(self):
        repo = self.make_repo()
        from toto.repo import git_cli
        log = git_cli.log_all(repo.worktree)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["message"], "Initial commit")
        self.assertEqual(services.repo_status(repo)["dirty"], [])

    def test_commit_flow(self):
        repo = self.make_repo()
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"v2\n")
        status = services.repo_status(repo)
        self.assertEqual([e["path"] for e in status["dirty"]], ["notes.txt"])
        self.assertIn("secret.txt", status["encrypted_skipped"])
        sha = services.commit(repo, self.user, "second version")
        self.assertEqual(len(sha), 40)
        self.assertEqual(services.repo_status(repo)["dirty"], [])

    def test_commit_requires_changes_and_message(self):
        repo = self.make_repo()
        with self.assertRaises(services.RepoError):
            services.commit(repo, self.user, "   ")
        with self.assertRaises(services.RepoError):
            services.commit(repo, self.user, "no changes")

    def test_branch_merge_flow_updates_vault(self):
        repo = self.make_repo()
        services.branch_create(repo, "feature", checkout=True, user=self.user)
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"feature edit\n")
        services.commit(repo, self.user, "feature edit")
        # switch back: vault content must roll back to main's version
        services.checkout(repo, "main", self.user)
        self.assertEqual(
            VaultFile.objects.get(pk=self.f_notes.pk).file.read(), b"hello notes\n"
        )
        result = services.merge(repo, "feature", self.user)
        self.assertTrue(result["ok"])
        self.assertEqual(
            VaultFile.objects.get(pk=self.f_notes.pk).file.read(), b"feature edit\n"
        )

    def test_merge_conflict_rolls_back(self):
        from toto.repo import git_cli
        repo = self.make_repo()
        services.branch_create(repo, "feature", checkout=False, user=self.user)
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"main line\n")
        services.commit(repo, self.user, "main edit")
        services.checkout(repo, "feature", self.user)
        with self.f_notes.file.open("wb") as fh:
            fh.write(b"feature line\n")
        services.commit(repo, self.user, "feature edit")
        services.checkout(repo, "main", self.user)
        with self.assertRaises(git_cli.MergeConflict) as ctx:
            services.merge(repo, "feature", self.user)
        self.assertEqual(ctx.exception.paths, ["notes.txt"])
        # vault untouched by the aborted merge
        self.assertEqual(
            VaultFile.objects.get(pk=self.f_notes.pk).file.read(), b"main line\n"
        )
