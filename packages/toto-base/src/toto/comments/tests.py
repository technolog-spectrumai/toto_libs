from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from . import services
from .models import Comment

User = get_user_model()


class CommentServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ada = User.objects.create_user("ada", password="x")
        cls.bob = User.objects.create_user("bob", password="x")
        cls.staff = User.objects.create_user("staff", password="x", is_staff=True)

    def test_add_edit_and_withdraw_by_the_author(self):
        c = services.add(self.ada, "  hello  ")
        self.assertEqual(c.body, "hello")
        services.edit(c, self.ada, "hello again")
        c.refresh_from_db()
        self.assertEqual(c.body, "hello again")
        self.assertIsNotNone(c.edited_at)
        services.soft_delete(c, self.ada)
        c.refresh_from_db()
        self.assertTrue(c.is_deleted)
        self.assertEqual(Comment.objects.count(), 1)      # withdrawn, not erased

    def test_nobody_else_edits_or_withdraws(self):
        c = services.add(self.ada, "mine")
        with self.assertRaises(PermissionDenied):
            services.edit(c, self.bob, "theirs")
        with self.assertRaises(PermissionDenied):
            services.soft_delete(c, self.bob)

    def test_staff_may(self):
        c = services.add(self.ada, "mine")
        services.edit(c, self.staff, "moderated")
        services.soft_delete(c, self.staff)
        self.assertTrue(Comment.objects.get(pk=c.pk).is_deleted)

    def test_empty_and_oversized_bodies_are_refused(self):
        with self.assertRaises(ValidationError):
            services.add(self.ada, "   ")
        with self.assertRaises(ValidationError):
            services.add(self.ada, "x" * (services.MAX_BODY + 1))

    def test_an_anonymous_user_cannot_comment(self):
        from django.contrib.auth.models import AnonymousUser

        with self.assertRaises(PermissionDenied):
            services.add(AnonymousUser(), "hi")

    def test_a_withdrawn_comment_cannot_be_answered_or_edited(self):
        c = services.add(self.ada, "x")
        services.soft_delete(c, self.ada)
        with self.assertRaises(ValidationError):
            services.add(self.bob, "reply", reply_to=c)
        with self.assertRaises(ValidationError):
            services.edit(c, self.ada, "back")

    def test_thread_orders_replies_under_their_parent(self):
        first = services.add(self.ada, "first")
        second = services.add(self.bob, "second")
        reply = services.add(self.bob, "re: first", reply_to=first)
        rows = services.thread(Comment.objects.all())
        self.assertEqual([r.pk for r in rows], [first.pk, reply.pk, second.pk])
        self.assertEqual([r.depth for r in rows], [0, 1, 0])
