"""``may_read`` clause by clause with bucket clearances in play (2026-09-30), its queryset twin
``accessible_files`` (including ``include_public``), the tree built on it,
the attach checks, and the content/mirror guards in ``access``.

One rule, several spellings: most tests here ask the per-object answer and
the queryset answer the same question and expect the same reply.
"""

import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.base import ContentFile
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Clearance, Community
from toto.vault import access, attach
from toto.vault.access import may_read
from toto.vault.filetree import accessible_files, build_file_tree
from toto.vault.models import Bucket, BucketClearance, VaultDirectory, VaultFile
from toto.vault.plugins import VaultAccessPlugin

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-access-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.internal = Clearance.objects.create(name="internal", slug="internal")
        cls.confidential = Clearance.objects.create(name="confidential", slug="confidential")
        cls.devs = Community.objects.create(name="devs", slug="devs")
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.member = User.objects.create_user("member", password="pw")
        cls.member_person = Person.objects.create(user=cls.member, display_name="Member")
        cls.member_person.clearances.add(cls.internal)
        cls.senior = User.objects.create_user("senior", password="pw")
        Person.objects.create(user=cls.senior, display_name="Senior").clearances.add(cls.confidential)
        cls.dev = User.objects.create_user("dev", password="pw")
        Person.objects.create(user=cls.dev, display_name="Dev").communities.add(cls.devs)
        cls.nobody = User.objects.create_user("nobody", password="pw")      # no Person at all
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)
        cls.theirs = Bucket.objects.create(name="Theirs", slug="theirs", owner=cls.nobody)

    _n = 0

    def file(self, *, owner=None, public=False, kept_to=(), bucket="default", directory=None,
             title=None, file_type="text"):
        """A file; ``kept_to`` keeps its BUCKET — a fresh one of the owner's
        unless ``bucket`` names one (clearances sit on buckets, never files)."""
        type(self)._n += 1
        title = title or f"f{self._n}.txt"
        if bucket == "default":
            bucket = (Bucket.objects.create(name=f"Kept {self._n}", slug=f"kept-{self._n}",
                                            owner=self.owner) if kept_to else self.bucket)
        vault_file = VaultFile(owner=owner or self.owner, title=title, key=f"k-{self._n}",
                               file_type=file_type, is_public=public, bucket=bucket,
                               directory=directory)
        vault_file.file.save(title, ContentFile(b"bytes"), save=False)
        vault_file.save()
        for clearance in kept_to:
            BucketClearance.objects.get_or_create(bucket=bucket, clearance=clearance)
        return vault_file

    def agree(self, user, vault_file, expected):
        """The per-object rule and the queryset rule give the same answer."""
        self.assertIs(may_read(user, vault_file), expected, "may_read")
        self.assertIs(accessible_files(user).filter(pk=vault_file.pk).exists(), expected,
                      "accessible_files")


class MayReadWithClearancesTests(_Fixture):
    def test_a_bucket_clearance_grants_a_private_file_to_its_holders(self):
        self.agree(self.member, self.file(kept_to=[self.internal]), True)

    def test_one_clearance_of_several_is_enough(self):
        f = self.file(kept_to=[self.internal, self.confidential])
        self.agree(self.senior, f, True)
        self.agree(self.member, f, True)

    def test_a_user_with_no_person_is_in_no_clearance(self):
        self.agree(self.nobody, self.file(public=True, kept_to=[self.internal]), False)

    def test_a_functional_community_member_is_not_a_clearance_member(self):
        self.agree(self.dev, self.file(public=True, kept_to=[self.internal]), False)

    def test_the_bucket_owner_loses_a_file_in_a_bucket_kept_to_a_clearance_they_lack(self):
        f = self.file(bucket=self.theirs, kept_to=[self.internal])
        self.agree(self.nobody, f, False)

    def test_the_bucket_owner_reads_it_again_once_it_is_open(self):
        f = self.file(bucket=self.theirs)
        self.agree(self.nobody, f, True)

    def test_a_holder_needs_no_place_on_the_folder_acl(self):
        directory = VaultDirectory.objects.create(name="d", bucket=self.bucket, owner=self.owner)
        directory.allowed_users.add(self.nobody)
        f = self.file(directory=directory, bucket=self.bucket, kept_to=[self.internal])
        self.agree(self.member, f, True)
        self.agree(self.nobody, f, False)

    def test_leaving_the_clearance_takes_the_file_away(self):
        f = self.file(kept_to=[self.internal])
        self.agree(self.member, f, True)
        self.member_person.clearances.remove(self.internal)
        self.agree(self.member, f, False)

    def test_the_owner_loses_a_file_in_a_bucket_kept_to_a_clearance_they_lack(self):
        self.agree(self.owner, self.file(public=True, kept_to=[self.confidential]), False)

    def test_an_owner_who_holds_a_clearance_of_the_bucket_reads_it(self):
        f = self.file(owner=self.member, kept_to=[self.internal])
        self.agree(self.member, f, True)

    def test_unkeeping_the_bucket_gives_the_file_back_to_the_vaults_rule(self):
        f = self.file(public=True, kept_to=[self.internal])
        self.agree(self.nobody, f, False)
        BucketClearance.objects.filter(bucket=f.bucket).delete()
        self.agree(self.nobody, f, True)
        self.agree(self.owner, f, True)

    def test_none_and_anonymous_get_the_public_arm_only(self):
        public, private = self.file(public=True), self.file()
        for visitor in (None, AnonymousUser()):
            self.assertTrue(may_read(visitor, public))
            self.assertFalse(may_read(visitor, private))

    def test_a_bucketless_private_file_is_its_owners_alone(self):
        f = self.file(bucket=None)
        self.agree(self.owner, f, True)
        self.agree(self.nobody, f, False)
        self.agree(self.root, f, True)


class AccessibleFilesTests(_Fixture):
    def test_include_public_false_drops_a_strangers_public_file(self):
        f = self.file(public=True)
        self.assertIn(f, accessible_files(self.nobody))
        self.assertNotIn(f, accessible_files(self.nobody, include_public=False))

    def test_include_public_false_keeps_every_claim(self):
        own = self.file(owner=self.nobody, bucket=None)
        in_my_bucket = self.file(bucket=self.theirs)
        directory = VaultDirectory.objects.create(name="s", bucket=self.bucket, owner=self.owner)
        directory.allowed_users.add(self.nobody)
        shared = self.file(directory=directory)
        self.assertEqual(set(accessible_files(self.nobody, include_public=False)),
                         {own, in_my_bucket, shared})

    def test_include_public_false_does_not_undo_a_clearance(self):
        f = self.file(bucket=self.theirs, kept_to=[self.internal])
        self.assertNotIn(f, accessible_files(self.nobody, include_public=False))

    def test_the_scopes_narrow(self):
        a = self.file(file_type="pdf")
        b = self.file(file_type="text")
        c = self.file(bucket=self.theirs, file_type="pdf")
        self.assertEqual(set(accessible_files(self.root, file_types=["pdf"])), {a, c})
        self.assertEqual(set(accessible_files(self.root, bucket=self.bucket)), {a, b})
        self.assertEqual(set(accessible_files(self.owner, exclude_pk=a.pk)), {b, c})

    def test_a_superuser_is_still_scoped_by_the_filters(self):
        a = self.file(file_type="pdf", kept_to=[self.confidential])
        self.file(file_type="text")
        self.assertEqual(list(accessible_files(self.root, file_types=("pdf",))), [a])

    def test_a_file_kept_to_two_of_my_clearances_is_listed_once(self):
        Person.objects.get(user=self.member).clearances.add(self.confidential)
        f = self.file(kept_to=[self.internal, self.confidential])
        self.assertEqual(list(accessible_files(self.member).filter(pk=f.pk)), [f])


class FileTreeTests(_Fixture):
    def test_files_group_by_bucket_then_folder_path(self):
        top = VaultDirectory.objects.create(name="top", bucket=self.bucket, owner=self.owner)
        sub = VaultDirectory.objects.create(name="sub", bucket=self.bucket, owner=self.owner,
                                            parent=top)
        self.file(title="root.txt")
        self.file(title="deep.md", directory=sub)
        tree = build_file_tree(self.owner)
        self.assertEqual([node["bucket"] for node in tree], [self.bucket])
        groups = tree[0]["groups"]
        self.assertEqual([g["dir"] for g in groups], ["", "top/sub"])
        self.assertEqual(groups[1]["dir_id"], sub.pk)
        self.assertIsNone(groups[0]["dir_id"])
        row = groups[1]["files"][0]
        self.assertEqual(row["title"], "deep.md")
        self.assertEqual(row["mimetype"], "text/markdown")

    def test_buckets_come_sorted_and_bucketless_files_first(self):
        self.file(title="loose.txt", bucket=None)
        self.file(title="t.txt", bucket=self.theirs)
        self.file(title="o.txt")
        tree = build_file_tree(self.root)
        self.assertEqual([n["bucket"].name if n["bucket"] else "" for n in tree],
                         ["", "Owned", "Theirs"])

    def test_the_tree_only_shows_what_the_reader_may_read(self):
        self.file(title="kept.txt", kept_to=[self.confidential])
        self.file(title="open.txt", public=True)
        titles = [r["title"] for n in build_file_tree(self.nobody)
                  for g in n["groups"] for r in g["files"]]
        self.assertEqual(titles, ["open.txt"])

    def test_a_given_queryset_is_exactly_what_is_drawn(self):
        a = self.file(title="a.txt")
        self.file(title="b.txt")
        tree = build_file_tree(self.owner, queryset=VaultFile.objects.filter(pk=a.pk))
        self.assertEqual([r["title"] for g in tree[0]["groups"] for r in g["files"]], ["a.txt"])

    def test_the_limit_caps_the_rows(self):
        for _ in range(4):
            self.file()
        rows = [r for n in build_file_tree(self.owner, limit=2) for g in n["groups"]
                for r in g["files"]]
        self.assertEqual(len(rows), 2)


class AttachWithClearancesTests(_Fixture):
    def test_a_bucket_owner_cannot_attach_a_file_kept_away_from_them(self):
        f = self.file(bucket=self.theirs, kept_to=[self.internal])
        with self.assertRaises(Http404):
            attach.validate_reference(self.nobody, f.pk)

    def test_the_owner_cannot_attach_their_file_in_a_bucket_kept_away_from_them(self):
        f = self.file(kept_to=[self.confidential])
        with self.assertRaises(Http404):
            attach.validate_reference(self.owner, str(f.pk))

    def test_a_holder_attaches_their_own_kept_file(self):
        f = self.file(owner=self.senior, kept_to=[self.confidential])
        self.assertEqual(attach.validate_reference(self.senior, str(f.pk)), f)

    def test_a_non_numeric_or_empty_pk_is_the_same_404(self):
        for pk in (None, "", 0, "abc", "1.5", object()):
            with self.assertRaises(Http404):
                attach.validate_reference(self.owner, pk)

    def test_keeping_the_bucket_to_a_clearance_hides_an_existing_attachment(self):
        f = self.file(public=True)
        self.assertTrue(attach.readable(self.nobody, f))
        BucketClearance.objects.create(bucket=f.bucket, clearance=self.internal)
        self.assertFalse(attach.readable(self.nobody, f))
        self.assertTrue(attach.readable(self.member, f))

    def test_visible_reads_a_named_attribute_and_skips_missing_files(self):
        class Row:
            def __init__(self, doc):
                self.doc = doc

        kept, open_ = self.file(kept_to=[self.internal]), self.file(public=True)
        rows = [Row(kept), Row(open_), Row(None)]
        self.assertEqual([r.doc for r in attach.visible(self.nobody, rows, attr="doc")], [open_])
        self.assertEqual([r.doc for r in attach.visible(self.member, rows, attr="doc")],
                         [kept, open_])


class _Lender:
    def __init__(self, answer=True, boom=False):
        self.answer, self.boom = answer, boom

    def may_edit(self, user, vault_file):
        if self.boom:
            raise RuntimeError("the lending app fell over")
        return self.answer


class MayEditViaAppTests(_Fixture):
    def test_no_file_or_no_login_never_widens(self):
        with patch.dict(VaultAccessPlugin.registry, {"text": _Lender()}):
            self.assertFalse(access.may_edit_via_app(self.member, None))
            self.assertFalse(access.may_edit_via_app(None, self.file()))
            self.assertFalse(access.may_edit_via_app(AnonymousUser(), self.file()))

    def test_a_type_nobody_lends_is_not_writable(self):
        with patch.dict(VaultAccessPlugin.registry, {}, clear=True):
            self.assertFalse(access.may_edit_via_app(self.member, self.file()))

    def test_the_lending_app_decides_for_its_type(self):
        f = self.file()
        with patch.dict(VaultAccessPlugin.registry, {"text": _Lender(True)}):
            self.assertTrue(access.may_edit_via_app(self.member, f))
        with patch.dict(VaultAccessPlugin.registry, {"text": _Lender(False)}):
            self.assertFalse(access.may_edit_via_app(self.member, f))

    def test_a_lending_app_that_raises_refuses(self):
        with patch.dict(VaultAccessPlugin.registry, {"text": _Lender(boom=True)}):
            self.assertFalse(access.may_edit_via_app(self.member, self.file()))


class WhereTheBytesAreTests(_Fixture):
    def test_local_content_and_its_queryset_twin_agree(self):
        s3 = Bucket.objects.create(name="S3", slug="s3", owner=self.owner, storage_backend="s3")
        blank = Bucket.objects.create(name="Blank", slug="blank", owner=self.owner)
        Bucket.objects.filter(pk=blank.pk).update(storage_backend="")
        blank.refresh_from_db()
        files = {
            "loose": self.file(bucket=None),
            "local": self.file(),
            "blank": self.file(bucket=blank),
            "s3": self.file(bucket=s3),
        }
        expected = {"loose": True, "local": True, "blank": True, "s3": False}
        self.assertEqual({k: access.is_local_content(f) for k, f in files.items()}, expected)
        local_pks = set(VaultFile.objects.filter(access.local_content_q())
                        .values_list("pk", flat=True))
        self.assertEqual({k: f.pk in local_pks for k, f in files.items()}, expected)
        self.assertFalse(access.is_local_content(None))

    def test_the_remote_refusal_names_the_bucket_and_the_way_out(self):
        s3 = Bucket.objects.create(name="Far away", slug="far", owner=self.owner,
                                   storage_backend="s3")
        f = self.file(bucket=s3, title="report.txt")
        response = access.remote_lock_response(RequestFactory().get("/"), f)
        self.assertEqual(response.status_code, 403)
        body = response.content.decode()
        self.assertIn("'report.txt' lives in a remote bucket (Far away)", body)
        self.assertIn("Downloading it still works.", body)

    def test_a_mirror_row_is_refused_with_json(self):
        f = self.file(title="peer.txt")
        self.assertFalse(access.is_mirror_row(f))
        f.origin = "mirror"
        self.assertTrue(access.is_mirror_row(f))
        response = access.mirror_lock_response(f)
        self.assertEqual(response.status_code, 403)
        import json

        payload = json.loads(response.content)
        self.assertFalse(payload["ok"])
        self.assertIn("change it on the origin host", payload["error"])

    def test_the_encrypted_lock_page_is_a_403(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        request = RequestFactory().get("/")
        request.user = self.owner
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.backends.db import SessionStore

        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        response = access.encrypted_lock_response(request, self.file(title="sealed.txt"))
        self.assertEqual(response.status_code, 403)
        self.assertIn("sealed.txt", response.content.decode())
