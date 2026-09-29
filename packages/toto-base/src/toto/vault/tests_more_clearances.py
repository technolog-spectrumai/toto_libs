"""The file-clearances door (``files/<pk>/access/``) and its helpers, past what
tests_clearances pins: the ``?next=`` open-redirect guard, who may manage, what
a forged form can and cannot tick, and the one audit record a change leaves.
"""

import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault import clearances
from toto.vault.access import may_read
from toto.vault.models import Bucket, VaultFile, VaultFileClearance

User = get_user_model()

ACTION = "VAULT.FILE.CLEARANCES_CHANGED"


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-clearances-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.internal = Clearance.objects.create(name="internal", slug="internal")
        cls.confidential = Clearance.objects.create(name="confidential", slug="confidential")
        cls.owner = User.objects.create_user("owner", password="pw")
        Person.objects.create(user=cls.owner, display_name="Owner").clearances.add(cls.internal)
        cls.member = User.objects.create_user("member", password="pw")
        Person.objects.create(user=cls.member, display_name="Member").clearances.add(cls.internal)
        cls.stranger = User.objects.create_user("stranger", password="pw")
        cls.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        cls.root = User.objects.create_superuser("root", "r@e.com", "pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)

    _n = 0

    def file(self, *, public=False, kept_to=(), title="deck.txt", bucket=None):
        type(self)._n += 1
        vault_file = VaultFile(owner=self.owner, title=title, key=f"m-{self._n}",
                               file_type="text", is_public=public,
                               bucket=bucket or self.bucket)
        vault_file.file.save(title, ContentFile(b"the contents"), save=False)
        vault_file.save()
        for clearance in kept_to:
            VaultFileClearance.objects.create(file=vault_file, clearance=clearance)
        return vault_file

    def url(self, vault_file, **query):
        base = reverse("vault:file_access", args=[vault_file.pk])
        if query:
            from django.utils.http import urlencode

            return f"{base}?{urlencode(query)}"
        return base

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class NextRedirectGuardTests(_Fixture):
    """``next`` is where the sheet or deck that linked here wants its reader
    back. It is attacker-controllable, so only a same-site target survives."""

    def post(self, vault_file, next_url, **extra):
        self.client.force_login(self.owner)
        return self.client.post(self.url(vault_file),
                                {"clearance": [self.internal.pk], "next": next_url, **extra})

    def test_a_same_site_path_is_where_the_owner_lands(self):
        f = self.file()
        response = self.post(f, "/sheets/7/")
        self.assertRedirects(response, "/sheets/7/", fetch_redirect_response=False)

    def test_an_absolute_url_on_this_host_is_honoured(self):
        f = self.file()
        response = self.post(f, "http://testserver/sheets/7/")
        self.assertRedirects(response, "http://testserver/sheets/7/",
                             fetch_redirect_response=False)

    def test_another_site_is_dropped_for_the_access_page_itself(self):
        f = self.file()
        response = self.post(f, "https://evil.example/phish")
        self.assertRedirects(response, self.url(f), fetch_redirect_response=False)

    def test_a_protocol_relative_url_is_dropped(self):
        f = self.file()
        response = self.post(f, "//evil.example/phish")
        self.assertRedirects(response, self.url(f), fetch_redirect_response=False)

    def test_a_backslash_disguised_host_is_dropped(self):
        f = self.file()
        response = self.post(f, "/\\evil.example/phish")
        self.assertRedirects(response, self.url(f), fetch_redirect_response=False)

    def test_a_javascript_url_is_dropped(self):
        f = self.file()
        response = self.post(f, "javascript:alert(1)")
        self.assertRedirects(response, self.url(f), fetch_redirect_response=False)

    def test_the_guard_still_saves_the_change_it_refused_to_redirect_for(self):
        f = self.file()
        self.post(f, "https://evil.example/")
        self.assertEqual([c.name for c in clearances.clearances_of(f)], ["internal"])

    def test_next_in_the_query_string_is_read_on_a_post_too(self):
        f = self.file()
        self.client.force_login(self.owner)
        response = self.client.post(self.url(f, next="/decks/3/"), {"clearance": [self.internal.pk]})
        self.assertRedirects(response, "/decks/3/", fetch_redirect_response=False)

    def test_the_page_never_carries_a_hostile_next_into_its_form(self):
        f = self.file()
        self.client.force_login(self.owner)
        response = self.client.get(self.url(f, next="https://evil.example/phish"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["next"], "")
        body = response.content.decode()
        self.assertIn('<input type="hidden" name="next" value="">', body)
        self.assertNotIn('value="https://evil.example', body)

    def test_the_page_carries_a_same_site_next_into_its_form(self):
        f = self.file()
        self.client.force_login(self.owner)
        response = self.client.get(self.url(f, next="/sheets/7/"))
        self.assertEqual(response.context["next"], "/sheets/7/")


class WhoMayManageTests(_Fixture):
    def test_nobody_logged_out_manages_anything(self):
        f = self.file()
        self.assertFalse(clearances.may_manage(None, f))
        self.assertFalse(clearances.may_manage(AnonymousUser(), f))

    def test_the_owner_and_a_superuser_do(self):
        f = self.file()
        self.assertTrue(clearances.may_manage(self.owner, f))
        self.assertTrue(clearances.may_manage(self.root, f))

    def test_a_reader_staff_or_member_does_not(self):
        f = self.file(public=True, kept_to=[self.internal])
        self.assertTrue(may_read(self.member, f))
        self.assertFalse(clearances.may_manage(self.member, f))
        self.assertFalse(clearances.may_manage(self.staff, f))

    def test_a_logged_out_visitor_is_sent_to_log_in(self):
        f = self.file(public=True)
        response = self.client.get(self.url(f))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_missing_file_is_404(self):
        self.client.force_login(self.root)
        response = self.client.get(reverse("vault:file_access", args=[987654]))
        self.assertEqual(response.status_code, 404)

    def test_only_get_and_post_are_answered(self):
        f = self.file()
        self.client.force_login(self.owner)
        self.assertEqual(self.client.put(self.url(f)).status_code, 405)
        self.assertEqual(self.client.delete(self.url(f)).status_code, 405)

    def test_staff_who_may_read_a_public_file_may_not_decide_who_reads_it(self):
        f = self.file(public=True)
        self.client.force_login(self.staff)
        response = self.client.post(self.url(f), {"clearance": [self.internal.pk]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(clearances.clearances_of(f), [])

    def test_the_bucket_owner_who_reads_it_may_not_decide_either(self):
        theirs = Bucket.objects.create(name="Theirs", slug="theirs", owner=self.stranger)
        f = self.file(bucket=theirs)
        self.assertTrue(may_read(self.stranger, f))
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url(f)).status_code, 403)

    def test_a_superuser_may_keep_somebody_elses_file_and_is_the_recorded_actor(self):
        f = self.file()
        self.client.force_login(self.root)
        self.client.post(self.url(f), {"clearance": [self.confidential.pk]})
        self.assertEqual([c.name for c in clearances.clearances_of(f)], ["confidential"])
        self.assertEqual(AuditRecord.objects.get(action=ACTION).actor_user, self.root)


class TickingTests(_Fixture):
    def test_saving_the_same_clearances_says_so_and_records_nothing(self):
        f = self.file(kept_to=[self.internal])
        self.client.force_login(self.owner)
        response = self.client.post(self.url(f), {"clearance": [self.internal.pk]})
        self.assertIn("Nothing changed.", self.messages(response))
        self.assertFalse(AuditRecord.objects.filter(action=ACTION).exists())

    def test_a_change_says_saved(self):
        f = self.file()
        self.client.force_login(self.owner)
        response = self.client.post(self.url(f), {"clearance": [self.internal.pk]})
        self.assertIn("Saved.", self.messages(response))

    def test_forged_ids_are_ignored_and_the_real_one_kept(self):
        f = self.file()
        self.client.force_login(self.owner)
        self.client.post(self.url(f), {"clearance": [
            "abc", "-1", "1" * 30, "", str(self.internal.pk)]})
        self.assertEqual([c.name for c in clearances.clearances_of(f)], ["internal"])

    def test_an_owner_cannot_tick_a_clearance_they_are_not_in(self):
        f = self.file()
        self.client.force_login(self.owner)                  # in internal, not in confidential
        self.client.post(self.url(f), {"clearance": [self.confidential.pk]})
        self.assertEqual(clearances.clearances_of(f), [])

    def test_a_clearance_already_keeping_the_file_is_offered_to_an_owner_outside_it(self):
        # A superuser kept the owner's file to confidential; the owner must still be
        # able to see that clearance ticked, and untick it.
        f = self.file(kept_to=[self.confidential])
        self.client.force_login(self.owner)
        response = self.client.get(self.url(f))
        rows = {row["clearance"].name: row["on"] for row in response.context["clearances"]}
        self.assertEqual(rows, {"confidential": True, "internal": False})
        self.assertTrue(response.context["restricted"])
        self.client.post(self.url(f), {})
        self.assertEqual(clearances.clearances_of(f), [])

    def test_an_open_file_is_not_marked_restricted(self):
        f = self.file()
        self.client.force_login(self.owner)
        self.assertFalse(self.client.get(self.url(f)).context["restricted"])

    def test_only_digit_strings_become_ids(self):
        self.assertEqual(clearances._ids(["12", "abc", " 3", "-4", "1" * 19, "١", "²"]),
                         {12})


class SetClearancesTests(_Fixture):
    def test_the_audit_record_names_the_file_and_both_sides(self):
        f = self.file(kept_to=[self.internal])
        before, after = clearances.set_clearances(f, [self.confidential, self.internal], actor=self.root)
        self.assertEqual((before, after), (["internal"], ["confidential", "internal"]))
        record = AuditRecord.objects.get(action=ACTION)
        self.assertEqual(record.metadata["file"], f.pk)
        self.assertEqual(record.metadata["title"], "deck.txt")
        self.assertEqual(record.metadata["file_type"], "text")
        self.assertEqual(record.metadata["before"], ["internal"])
        self.assertEqual(record.metadata["after"], ["confidential", "internal"])
        self.assertFalse(record.metadata["open"])

    def test_the_file_is_kept_the_moment_the_rows_exist(self):
        f = self.file(public=True)
        self.assertTrue(may_read(self.stranger, f))
        clearances.set_clearances(f, [self.internal], actor=self.owner)
        self.assertFalse(may_read(self.stranger, f))
        self.assertTrue(may_read(self.member, f))

    def test_clearances_of_is_sorted_by_name(self):
        f = self.file(kept_to=[self.confidential, self.internal])
        self.assertEqual([c.name for c in clearances.clearances_of(f)], ["confidential", "internal"])


class AccessUrlTests(_Fixture):
    def test_without_next_it_is_the_bare_page(self):
        f = self.file()
        self.assertEqual(clearances.access_url(f), f"/vault/files/{f.pk}/access/")

    def test_next_is_url_encoded(self):
        f = self.file()
        self.assertEqual(clearances.access_url(f, "/sheets/1/?tab=a&b=c"),
                         f"/vault/files/{f.pk}/access/?next=%2Fsheets%2F1%2F%3Ftab%3Da%26b%3Dc")


class RowTests(_Fixture):
    def test_one_clearance_keeps_a_file_once(self):
        f = self.file(kept_to=[self.internal])
        with self.assertRaises(IntegrityError), transaction.atomic():
            VaultFileClearance.objects.create(file=f, clearance=self.internal)

    def test_deleting_the_file_takes_its_rows_with_it(self):
        f = self.file(kept_to=[self.internal, self.confidential])
        f.delete()
        self.assertFalse(VaultFileClearance.objects.exists())
        self.internal.delete()                     # no longer protected by anything


class ClearanceTargetKindTests(_Fixture):
    """``vault.file`` — the kind the socialhub's New clearance modal offers for
    files (2026-09-30, "what a clearance clears"): search, resolve and keep,
    with real rows. Sheets and decks are vault files too, but other kinds
    (primula, memo) claim their types; mirrored stubs are never offered."""

    KEY = "vault.file"

    def setUp(self):
        from toto.socialhub.plugins.clearance_plugins import kind

        self.kind = kind(self.KEY)

    def make(self, title, *, file_type="text", key=None, mirrored=False, public=False,
             kept_to=()):
        from toto.vault.models import FileOrigin

        type(self)._n += 1
        vault_file = VaultFile(owner=self.owner, title=title, key=key or f"k-{self._n}",
                               file_type=file_type, is_public=public, bucket=self.bucket,
                               origin=FileOrigin.MIRROR if mirrored else FileOrigin.NATIVE)
        if mirrored:
            vault_file.file.name = vault_file.key       # a stub: the bytes live on the peer
            vault_file.save()
        else:
            vault_file.file.save(f"{self._n}.bin", ContentFile(b"x"), save=False)
            vault_file.save()
        for clearance in kept_to:
            VaultFileClearance.objects.create(file=vault_file, clearance=clearance)
        return vault_file

    def found(self, q, **kw):
        return [row["pk"] for row in self.kind.search(q, **kw)]

    def names(self, vault_file):
        return [c.name for c in clearances.clearances_of(vault_file)]

    # -- registration --------------------------------------------------------

    def test_it_is_registered_under_its_key_with_a_title_and_an_icon(self):
        from django.utils import translation

        from toto.socialhub.plugins.clearance_plugins import kinds
        from toto.vault.plugins.clearance_plugins import VaultFileKind

        self.assertIsInstance(self.kind, VaultFileKind)
        self.assertIn(self.KEY, [k.get_key() for k in kinds()])
        self.assertEqual(self.kind.icon, "file")
        with translation.override("en"):
            self.assertEqual(str(self.kind.get_title()), "Files")

    def test_its_title_is_polish_in_polish(self):
        from django.utils import translation

        with translation.override("pl"):
            self.assertEqual(str(self.kind.get_title()), "Pliki")

    # -- search --------------------------------------------------------------

    def test_search_matches_the_title_case_insensitively(self):
        payroll = self.make("Payroll Q3.txt")
        notes = self.make("old payroll notes")
        self.make("minutes.txt")
        self.assertEqual(set(self.found("PAYROLL")), {payroll.pk, notes.pk})

    def test_search_matches_the_key_case_insensitively(self):
        budget = self.make("plain.txt", key="archive/Budget-2026.txt")
        self.assertEqual(self.found("budget-2026"), [budget.pk])

    def test_search_answers_pk_label_and_detail(self):
        f = self.make("Payroll.txt")
        self.assertEqual(self.kind.search("payroll"),
                         [{"pk": f.pk, "label": "Payroll.txt", "detail": "text · Owned"}])

    def test_a_file_without_a_title_is_labelled_by_its_key(self):
        f = self.make("", key="deep/untitled.bin")
        self.assertEqual(self.kind.search("untitled")[0]["label"], "deep/untitled.bin")

    def test_search_is_ordered_by_title_then_pk(self):
        c = self.make("c.txt")
        first_a = self.make("a.txt")
        b = self.make("b.txt")
        second_a = self.make("a.txt")
        self.assertEqual(self.found(".txt"), [first_a.pk, second_a.pk, b.pk, c.pk])

    def test_search_is_capped_by_limit(self):
        made = [self.make(f"report-{i}.txt") for i in range(5)]
        self.assertEqual(self.found("report", limit=3), [f.pk for f in made[:3]])
        self.assertEqual(len(self.found("report")), 5)

    def test_the_default_limit_is_the_socialhubs(self):
        from toto.socialhub.plugins.clearance_plugins import SEARCH_LIMIT

        for i in range(SEARCH_LIMIT + 2):
            self.make(f"bulk-{i:02}.txt")
        self.assertEqual(len(self.found("bulk")), SEARCH_LIMIT)

    # -- what the generic kind offers -----------------------------------------

    def test_a_mirrored_file_is_never_offered_nor_resolved(self):
        stub = self.make("Mirrored payroll.txt", mirrored=True)
        native = self.make("Native payroll.txt")
        self.assertEqual(self.found("payroll"), [native.pk])
        self.assertEqual(self.kind.resolve([stub.pk, native.pk]), [native])

    def test_types_the_host_s_other_kinds_claim_are_left_to_them(self):
        # primula.sheet claims "sheet", memo.deck "pxml" and "presentation".
        sheet = self.make("Q sheet", file_type="sheet")
        deck = self.make("Q deck", file_type="pxml")
        legacy = self.make("Q legacy deck", file_type="presentation")
        text = self.make("Q notes", file_type="text")
        self.assertEqual(self.found("Q "), [text.pk])
        self.assertEqual(self.kind.resolve([sheet.pk, deck.pk, legacy.pk, text.pk]), [text])

    def test_a_type_is_claimed_by_whichever_kind_is_registered(self):
        from unittest.mock import patch

        from toto.socialhub.plugins.clearance_plugins import ClearanceTargetPlugin
        from toto.vault.plugins.clearance_plugins import VaultFileKind

        class Tables(VaultFileKind):
            key = "test.tables"
            title = "Tables"
            order = 99
            vault_file_types = ("csv",)

        csv = self.make("Q table", file_type="csv")
        text = self.make("Q notes")
        self.assertEqual(set(self.found("Q ")), {csv.pk, text.pk})
        with patch.dict(ClearanceTargetPlugin.registry, {"test.tables": Tables()}):
            self.assertEqual(self.found("Q "), [text.pk])
            self.assertEqual(self.kind.resolve([csv.pk]), [])
            tables = ClearanceTargetPlugin.get("test.tables")
            self.assertEqual([r["pk"] for r in tables.search("Q ")], [csv.pk])
        self.assertEqual(set(self.found("Q ")), {csv.pk, text.pk})       # claim gone with it

    def test_with_no_other_kind_registered_a_sheet_is_just_a_file(self):
        from unittest.mock import patch

        from toto.socialhub.plugins.clearance_plugins import ClearanceTargetPlugin

        sheet = self.make("Q sheet", file_type="sheet")
        with patch.dict(ClearanceTargetPlugin.registry, {self.KEY: self.kind}, clear=True):
            self.assertEqual(self.found("Q "), [sheet.pk])

    # -- resolve ---------------------------------------------------------------

    def test_resolve_ignores_unknown_pks_and_is_ordered(self):
        b = self.make("b.txt")
        a = self.make("a.txt")
        self.assertEqual(self.kind.resolve([b.pk, 987654, a.pk]), [a, b])
        self.assertEqual(self.kind.resolve([987654]), [])
        self.assertEqual(self.kind.resolve([]), [])

    # -- keep ------------------------------------------------------------------

    def test_keep_adds_the_clearance_to_a_file_already_kept_to_another(self):
        f = self.make("Kept.txt", kept_to=[self.internal])
        self.assertEqual(self.kind.keep([f], self.confidential, actor=self.root), 1)
        self.assertEqual(self.names(f), ["confidential", "internal"])
        self.assertTrue(may_read(self.member, f))              # internal still reads it

    def test_keep_closes_an_open_public_file_to_everyone_outside_the_clearance(self):
        f = self.make("Open.txt", public=True)
        self.assertTrue(may_read(self.stranger, f))
        self.assertTrue(may_read(self.staff, f))
        self.kind.keep([f], self.internal, actor=self.root)
        self.assertFalse(may_read(self.stranger, f))
        self.assertFalse(may_read(self.staff, f))
        self.assertFalse(may_read(AnonymousUser(), f))
        self.assertTrue(may_read(self.member, f))
        self.assertTrue(may_read(self.owner, f))

    def test_keep_writes_the_vaults_own_audit_record_with_the_actor(self):
        f = self.make("Audited.txt")
        self.kind.keep([f], self.internal, actor=self.root)
        record = AuditRecord.objects.get(action=ACTION)
        self.assertEqual(record.actor_user, self.root)
        self.assertEqual(record.metadata["file"], f.pk)
        self.assertEqual((record.metadata["before"], record.metadata["after"]), ([], ["internal"]))
        self.assertFalse(record.metadata["open"])

    def test_keep_takes_several_files_and_records_one_change_each(self):
        files = [self.make(f"many-{i}.txt") for i in range(3)]
        self.assertEqual(self.kind.keep(files, self.internal, actor=self.root), 3)
        for f in files:
            self.assertEqual(self.names(f), ["internal"])
        self.assertEqual(AuditRecord.objects.filter(action=ACTION).count(), 3)

    def test_keeping_twice_is_a_no_op(self):
        f = self.make("Twice.txt", kept_to=[self.confidential])
        self.kind.keep([f], self.internal, actor=self.root)
        self.kind.keep([f], self.internal, actor=self.root)
        self.assertEqual(self.names(f), ["confidential", "internal"])
        self.assertEqual(VaultFileClearance.objects.filter(file=f).count(), 2)
        self.assertEqual(AuditRecord.objects.filter(action=ACTION).count(), 1)

    def test_keeping_a_file_to_the_clearance_it_already_has_records_nothing(self):
        f = self.make("Already.txt", kept_to=[self.internal])
        self.kind.keep([f], self.internal, actor=self.root)
        self.assertEqual(self.names(f), ["internal"])
        self.assertFalse(AuditRecord.objects.filter(action=ACTION).exists())
