"""The file-circles door (``files/<pk>/access/``) and its helpers, past what
tests_circles pins: the ``?next=`` open-redirect guard, who may manage, what
a forged form can and cannot tick, and the one audit record a change leaves.
"""

import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.circle_access import CircleRefused
from toto.socialhub.models import Community
from toto.vault import circles
from toto.vault.access import may_read
from toto.vault.models import Bucket, VaultFile, VaultFileCircle

User = get_user_model()

ACTION = "VAULT.FILE.CIRCLES_CHANGED"


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-circles-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.board = Community.objects.create(name="board", slug="board", is_circle=True)
        cls.seniors = Community.objects.create(name="seniors", slug="seniors", is_circle=True)
        cls.devs = Community.objects.create(name="devs", slug="devs")
        cls.owner = User.objects.create_user("owner", password="pw")
        Person.objects.create(user=cls.owner, display_name="Owner").communities.add(cls.board)
        cls.member = User.objects.create_user("member", password="pw")
        Person.objects.create(user=cls.member, display_name="Member").communities.add(cls.board)
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
        for circle in kept_to:
            VaultFileCircle.objects.create(file=vault_file, circle=circle)
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
                                {"circle": [self.board.pk], "next": next_url, **extra})

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
        self.assertEqual([c.name for c in circles.circles_of(f)], ["board"])

    def test_next_in_the_query_string_is_read_on_a_post_too(self):
        f = self.file()
        self.client.force_login(self.owner)
        response = self.client.post(self.url(f, next="/decks/3/"), {"circle": [self.board.pk]})
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
        self.assertFalse(circles.may_manage(None, f))
        self.assertFalse(circles.may_manage(AnonymousUser(), f))

    def test_the_owner_and_a_superuser_do(self):
        f = self.file()
        self.assertTrue(circles.may_manage(self.owner, f))
        self.assertTrue(circles.may_manage(self.root, f))

    def test_a_reader_staff_or_member_does_not(self):
        f = self.file(public=True, kept_to=[self.board])
        self.assertTrue(may_read(self.member, f))
        self.assertFalse(circles.may_manage(self.member, f))
        self.assertFalse(circles.may_manage(self.staff, f))

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
        response = self.client.post(self.url(f), {"circle": [self.board.pk]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(circles.circles_of(f), [])

    def test_the_bucket_owner_who_reads_it_may_not_decide_either(self):
        theirs = Bucket.objects.create(name="Theirs", slug="theirs", owner=self.stranger)
        f = self.file(bucket=theirs)
        self.assertTrue(may_read(self.stranger, f))
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url(f)).status_code, 403)

    def test_a_superuser_may_keep_somebody_elses_file_and_is_the_recorded_actor(self):
        f = self.file()
        self.client.force_login(self.root)
        self.client.post(self.url(f), {"circle": [self.seniors.pk]})
        self.assertEqual([c.name for c in circles.circles_of(f)], ["seniors"])
        self.assertEqual(AuditRecord.objects.get(action=ACTION).actor_user, self.root)


class TickingTests(_Fixture):
    def test_saving_the_same_circles_says_so_and_records_nothing(self):
        f = self.file(kept_to=[self.board])
        self.client.force_login(self.owner)
        response = self.client.post(self.url(f), {"circle": [self.board.pk]})
        self.assertIn("Nothing changed.", self.messages(response))
        self.assertFalse(AuditRecord.objects.filter(action=ACTION).exists())

    def test_a_change_says_saved(self):
        f = self.file()
        self.client.force_login(self.owner)
        response = self.client.post(self.url(f), {"circle": [self.board.pk]})
        self.assertIn("Saved.", self.messages(response))

    def test_forged_ids_are_ignored_and_the_real_one_kept(self):
        f = self.file()
        self.client.force_login(self.owner)
        self.client.post(self.url(f), {"circle": [
            "abc", "-1", "1" * 30, "", str(self.board.pk)]})
        self.assertEqual([c.name for c in circles.circles_of(f)], ["board"])

    def test_an_owner_cannot_tick_a_circle_they_are_not_in(self):
        f = self.file()
        self.client.force_login(self.owner)                  # in board, not in seniors
        self.client.post(self.url(f), {"circle": [self.seniors.pk]})
        self.assertEqual(circles.circles_of(f), [])

    def test_a_circle_already_keeping_the_file_is_offered_to_an_owner_outside_it(self):
        # A superuser kept the owner's file to seniors; the owner must still be
        # able to see that circle ticked, and untick it.
        f = self.file(kept_to=[self.seniors])
        self.client.force_login(self.owner)
        response = self.client.get(self.url(f))
        rows = {row["circle"].name: row["on"] for row in response.context["circles"]}
        self.assertEqual(rows, {"board": False, "seniors": True})
        self.assertTrue(response.context["restricted"])
        self.client.post(self.url(f), {})
        self.assertEqual(circles.circles_of(f), [])

    def test_an_open_file_is_not_marked_restricted(self):
        f = self.file()
        self.client.force_login(self.owner)
        self.assertFalse(self.client.get(self.url(f)).context["restricted"])

    def test_only_digit_strings_become_ids(self):
        self.assertEqual(circles._ids(["12", "abc", " 3", "-4", "1" * 19, "١", "²"]),
                         {12})


class SetCirclesTests(_Fixture):
    def test_the_audit_record_names_the_file_and_both_sides(self):
        f = self.file(kept_to=[self.board])
        before, after = circles.set_circles(f, [self.seniors, self.board], actor=self.root)
        self.assertEqual((before, after), (["board"], ["board", "seniors"]))
        record = AuditRecord.objects.get(action=ACTION)
        self.assertEqual(record.metadata["file"], f.pk)
        self.assertEqual(record.metadata["title"], "deck.txt")
        self.assertEqual(record.metadata["file_type"], "text")
        self.assertEqual(record.metadata["before"], ["board"])
        self.assertEqual(record.metadata["after"], ["board", "seniors"])
        self.assertFalse(record.metadata["open"])

    def test_a_functional_community_is_refused_and_nothing_moves(self):
        f = self.file(kept_to=[self.board])
        with self.assertRaises(CircleRefused):
            circles.set_circles(f, [self.devs], actor=self.root)
        self.assertEqual([c.name for c in circles.circles_of(f)], ["board"])
        self.assertFalse(AuditRecord.objects.filter(action=ACTION).exists())

    def test_the_file_is_kept_the_moment_the_rows_exist(self):
        f = self.file(public=True)
        self.assertTrue(may_read(self.stranger, f))
        circles.set_circles(f, [self.board], actor=self.owner)
        self.assertFalse(may_read(self.stranger, f))
        self.assertTrue(may_read(self.member, f))

    def test_circles_of_is_sorted_by_name(self):
        f = self.file(kept_to=[self.seniors, self.board])
        self.assertEqual([c.name for c in circles.circles_of(f)], ["board", "seniors"])


class AccessUrlTests(_Fixture):
    def test_without_next_it_is_the_bare_page(self):
        f = self.file()
        self.assertEqual(circles.access_url(f), f"/vault/files/{f.pk}/access/")

    def test_next_is_url_encoded(self):
        f = self.file()
        self.assertEqual(circles.access_url(f, "/sheets/1/?tab=a&b=c"),
                         f"/vault/files/{f.pk}/access/?next=%2Fsheets%2F1%2F%3Ftab%3Da%26b%3Dc")


class RowTests(_Fixture):
    def test_one_circle_keeps_a_file_once(self):
        f = self.file(kept_to=[self.board])
        with self.assertRaises(IntegrityError), transaction.atomic():
            VaultFileCircle.objects.create(file=f, circle=self.board)

    def test_the_admin_row_refuses_a_functional_community(self):
        f = self.file()
        with self.assertRaises(ValidationError):
            VaultFileCircle(file=f, circle=self.devs).full_clean()

    def test_deleting_the_file_takes_its_rows_with_it(self):
        f = self.file(kept_to=[self.board, self.seniors])
        f.delete()
        self.assertFalse(VaultFileCircle.objects.exists())
        self.board.delete()                     # no longer protected by anything
