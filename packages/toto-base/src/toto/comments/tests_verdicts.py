"""The thread tag with a caller's own rules (2026-10-06, stage 64): the
caller's verdicts in place of ``Comment.may_modify``, a price beside the
buttons, an ``op`` in the forms, and a withdrawal by a caller that has done
its own check. Without them the tag is what it always was."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from . import services
from .templatetags.comments_tags import comment_thread

User = get_user_model()


class VerdictTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ada = User.objects.create_user("ada", password="x")
        cls.staff = User.objects.create_user("staff", password="x", is_staff=True)
        cls.comment = services.add(cls.ada, "mine")

    def _context(self, user, **extra):
        request = RequestFactory().get("/")
        request.user = user
        return comment_thread({"request": request}, [self.comment], "/post/", "e", "d",
                              parent_pk="p", **extra)

    def test_without_verdicts_the_author_and_staff_get_both(self):
        for user in (self.ada, self.staff):
            row = self._context(user)["comments"][0]
            self.assertTrue(row.can_edit and row.can_withdraw and row.can_modify)
        context = self._context(self.ada)
        self.assertEqual(context["op"], "")
        self.assertEqual(context["price_metric"], "")

    def test_a_caller_s_verdict_hides_edit_from_staff(self):
        row = self._context(self.staff, verdicts={})["comments"][0]
        self.assertFalse(row.can_edit or row.can_withdraw or row.can_modify)
        row = self._context(self.staff,
                            verdicts={self.comment.pk: (False, True)})["comments"][0]
        self.assertFalse(row.can_edit)
        self.assertTrue(row.can_withdraw)

    def test_the_op_field_is_in_the_forms_when_asked_for(self):
        context = self._context(self.staff, verdicts={}, op=True,
                                price_metric="geography.comment")
        html = render_to_string("comments/_thread.html", context)
        self.assertEqual(html.count('name="op"'), 2)        # Reply, and Add a comment
        self.assertIn(f'value="{context["op"]}"', html)
        self.assertNotEqual(context["op"], context["comments"][0].op)
        self.assertNotIn(">Edit<", html)                      # the caller said no
        self.assertNotIn(">Withdraw<", html)
        plain = render_to_string("comments/_thread.html", self._context(self.staff, verdicts={}))
        self.assertNotIn('name="op"', plain)

    def test_a_moderator_s_withdrawal_is_the_caller_s_own_check(self):
        bob = User.objects.create_user("bob", password="x")
        with self.assertRaises(PermissionDenied):
            services.soft_delete(self.comment, bob)
        services.soft_delete(self.comment, bob, by_moderator=True)
        self.comment.refresh_from_db()
        self.assertTrue(self.comment.is_deleted)
