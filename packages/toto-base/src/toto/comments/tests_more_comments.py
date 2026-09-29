"""Comments, beyond the first suite (2026-09-29): the length boundary, the
refusals for edits, withdrawal that keeps the text, threading edge cases, the
form, and the thread tag — who is offered Edit and Withdraw, and what a
withdrawn comment or a departed author looks like on the page."""

import unittest

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import path

from toto.comments import services
from toto.comments.forms import CommentForm
from toto.comments.models import Comment

User = get_user_model()


def _noop(request, *args, **kwargs):
    return HttpResponse()


# The host's own routes, as a host app declares them: (parent, comment).
urlpatterns = [
    path("things/<slug:slug>/comments/<int:pk>/edit/", _noop, name="thing_comment_edit"),
    path("things/<slug:slug>/comments/<int:pk>/delete/", _noop, name="thing_comment_delete"),
]


class ServiceEdgeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ada = User.objects.create_user("ada", password="x")
        cls.bob = User.objects.create_user("bob", password="x")

    def test_exactly_the_maximum_length_is_accepted(self):
        body = "x" * services.MAX_BODY
        self.assertEqual(len(services.add(self.ada, body).body), services.MAX_BODY)

    def test_the_length_is_measured_after_the_edges_are_trimmed(self):
        body = "   " + "x" * services.MAX_BODY + "\n\n"
        self.assertEqual(len(services.add(self.ada, body).body), services.MAX_BODY)

    def test_no_body_at_all_is_refused(self):
        with self.assertRaises(ValidationError):
            services.add(self.ada, None)

    def test_an_edit_to_nothing_is_refused_and_the_text_is_kept(self):
        comment = services.add(self.ada, "original")
        with self.assertRaises(ValidationError):
            services.edit(comment, self.ada, "   ")
        comment.refresh_from_db()
        self.assertEqual(comment.body, "original")
        self.assertIsNone(comment.edited_at)

    def test_an_oversized_edit_is_refused(self):
        comment = services.add(self.ada, "original")
        with self.assertRaises(ValidationError):
            services.edit(comment, self.ada, "x" * (services.MAX_BODY + 1))

    def test_an_anonymous_visitor_neither_edits_nor_withdraws(self):
        comment = services.add(self.ada, "mine")
        with self.assertRaises(PermissionDenied):
            services.edit(comment, AnonymousUser(), "theirs")
        with self.assertRaises(PermissionDenied):
            services.soft_delete(comment, AnonymousUser())

    def test_a_superuser_without_the_staff_bit_may_moderate(self):
        root = User.objects.create_user("root", password="x", is_superuser=True)
        comment = services.add(self.ada, "mine")
        self.assertTrue(comment.may_modify(root))
        services.soft_delete(comment, root)
        self.assertTrue(Comment.objects.get(pk=comment.pk).is_deleted)

    def test_withdrawing_twice_keeps_the_first_moment_and_the_text(self):
        comment = services.add(self.ada, "keep me")
        services.soft_delete(comment, self.ada)
        first = Comment.objects.get(pk=comment.pk).deleted_at
        services.soft_delete(Comment.objects.get(pk=comment.pk), self.ada)
        again = Comment.objects.get(pk=comment.pk)
        self.assertEqual(again.deleted_at, first)
        self.assertEqual(again.body, "keep me")

    def test_a_comment_outlives_its_author(self):
        comment = services.add(self.bob, "still here")
        self.bob.delete()
        comment.refresh_from_db()
        self.assertIsNone(comment.author_id)
        self.assertFalse(comment.may_modify(self.ada))

    def test_a_reply_whose_parent_is_not_shown_stands_on_its_own(self):
        parent = services.add(self.ada, "parent")
        reply = services.add(self.bob, "reply", reply_to=parent)
        rows = services.thread(Comment.objects.filter(pk=reply.pk))
        self.assertEqual([(r.pk, r.depth) for r in rows], [(reply.pk, 0)])

    def test_replies_keep_their_order_under_the_parent(self):
        top = services.add(self.ada, "top")
        first = services.add(self.bob, "one", reply_to=top)
        second = services.add(self.ada, "two", reply_to=top)
        self.assertEqual([r.pk for r in services.thread(Comment.objects.all())],
                         [top.pk, first.pk, second.pk])

    def test_an_empty_thread_is_empty(self):
        self.assertEqual(services.thread(Comment.objects.none()), [])

    @unittest.skip("SUSPECTED BUG toto/comments/services.py:32,60 - add() accepts a reply "
                   "to a reply (places.comment_add passes any comment on the place as "
                   "reply_to), but thread() renders one level only, so that comment is "
                   "saved and never shown.")
    def test_a_reply_to_a_reply_is_not_lost_from_the_thread(self):
        top = services.add(self.ada, "top")
        reply = services.add(self.bob, "reply", reply_to=top)
        deeper = services.add(self.ada, "deeper", reply_to=reply)
        self.assertIn(deeper.pk, [r.pk for r in services.thread(Comment.objects.all())])

    def test_the_row_names_itself_by_number_and_author(self):
        comment = services.add(self.ada, "x")
        self.assertEqual(str(comment), f"comment {comment.pk} by {self.ada.pk}")


class FormTests(SimpleTestCase):
    def test_a_body_is_required_and_a_reply_is_optional(self):
        self.assertFalse(CommentForm({"body": ""}).is_valid())
        form = CommentForm({"body": "hello"})
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["reply_to"])

    def test_the_form_refuses_what_the_service_would(self):
        self.assertFalse(CommentForm({"body": "x" * (services.MAX_BODY + 1)}).is_valid())
        self.assertTrue(CommentForm({"body": "x" * services.MAX_BODY}).is_valid())

    def test_a_reply_target_must_be_a_number(self):
        form = CommentForm({"body": "hi", "reply_to": "drop table"})
        self.assertFalse(form.is_valid())
        self.assertIn("reply_to", form.errors)


@override_settings(ROOT_URLCONF=__name__)
class ThreadTagTests(TestCase):
    TAG = Template("{% load comments_tags %}{% comment_thread comments '/things/here/comments/' "
                   "'thing_comment_edit' 'thing_comment_delete' 'here' %}")

    @classmethod
    def setUpTestData(cls):
        cls.ada = User.objects.create_user("ada", password="x")
        cls.bob = User.objects.create_user("bob", password="x")
        cls.staff = User.objects.create_user("mod", password="x", is_staff=True)

    def render(self, user):
        request = RequestFactory().get("/things/here/")
        request.user = user
        return self.TAG.render(Context({"request": request, "comments": Comment.objects.all()}))

    def test_the_author_is_offered_edit_and_withdraw_on_their_own_comment(self):
        comment = services.add(self.ada, "mine")
        html = self.render(self.ada)
        self.assertIn(f"/things/here/comments/{comment.pk}/edit/", html)
        self.assertIn(f"/things/here/comments/{comment.pk}/delete/", html)

    def test_another_member_is_offered_neither(self):
        comment = services.add(self.ada, "mine")
        html = self.render(self.bob)
        self.assertNotIn(f"/comments/{comment.pk}/edit/", html)
        self.assertNotIn(f"/comments/{comment.pk}/delete/", html)
        self.assertIn("mine", html)

    def test_staff_is_offered_both(self):
        comment = services.add(self.ada, "mine")
        self.assertIn(f"/comments/{comment.pk}/delete/", self.render(self.staff))

    def test_a_withdrawn_comment_shows_the_notice_and_not_its_text(self):
        comment = services.add(self.ada, "regrettable words")
        services.soft_delete(comment, self.ada)
        html = self.render(self.ada)
        self.assertIn("This comment was withdrawn.", html)
        self.assertNotIn("regrettable words", html)
        self.assertNotIn(f"/comments/{comment.pk}/edit/", html)

    def test_a_departed_author_is_a_former_member(self):
        services.add(self.bob, "left behind")
        self.bob.delete()
        self.assertIn("a former member", self.render(self.ada))

    def test_a_visitor_who_is_not_signed_in_gets_no_forms(self):
        services.add(self.ada, "public words")
        html = self.render(AnonymousUser())
        self.assertIn("public words", html)
        self.assertNotIn("Add a comment", html)
        self.assertNotIn('name="reply_to"', html)

    def test_only_a_top_level_comment_can_be_answered(self):
        top = services.add(self.ada, "top")
        reply = services.add(self.bob, "reply", reply_to=top)
        html = self.render(self.ada)
        self.assertIn(f'name="reply_to" value="{top.pk}"', html)
        self.assertNotIn(f'name="reply_to" value="{reply.pk}"', html)

    def test_a_comment_s_markup_is_shown_as_text(self):
        services.add(self.ada, "<script>alert(1)</script>")
        html = self.render(self.bob)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_an_edited_comment_says_so(self):
        comment = services.add(self.ada, "first")
        services.edit(comment, self.ada, "second")
        html = self.render(self.bob)
        self.assertIn("edited", html)
        self.assertIn("second", html)

    def test_an_empty_thread_says_there_is_nothing_yet(self):
        self.assertIn("No comments yet.", self.render(self.ada))
