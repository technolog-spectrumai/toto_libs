"""Mailboxes and their two custody regimes, and the Mail Guardian office.

The claims under test are the ones the design rests on: a personal mailbox
cannot be read without its owner's passphrase; the system mailbox can be
read by the platform because it must send unattended; there is never more
than one system mailbox; and platform mail refuses loudly rather than
sending from an identity nobody governs.

Run only where a gate stanza names this module.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from toto.people.models import Person
from toto.socialhub.models import Station

from . import guardian, keyring
from .models import Keyholder, Mailbox, MailboxKind, SentRecord

User = get_user_model()

PASSPHRASE = "correct horse battery staple"
MAIL_PASSWORD = "s3cret-imap-password"


def _account(user, **kwargs):
    defaults = dict(
        owner=user, kind=MailboxKind.PERSONAL, label="Work",
        email_address=f"{user.get_username()}@example.org",
        host="smtp.example.org", username=f"{user.get_username()}",
        imap_host="imap.example.org")
    defaults.update(kwargs)
    return Mailbox.objects.create(**defaults)


class PersonalCustodyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="x")
        cls.bob = User.objects.create_user("bob", password="x")

    def test_a_password_is_sealed_and_reads_back_with_the_passphrase(self):
        mailbox = _account(self.anna)
        keyring.store_password(mailbox, MAIL_PASSWORD, passphrase=PASSPHRASE)
        mailbox.refresh_from_db()

        self.assertIsNotNone(mailbox.secret_id)
        # The plaintext is nowhere in the row or in the secret's ciphertext.
        self.assertNotIn(MAIL_PASSWORD, str(mailbox.__dict__))
        self.assertNotIn(MAIL_PASSWORD.encode(),
                         bytes(mailbox.secret.ciphertext))

        self.assertEqual(
            keyring.read_password(mailbox, passphrase=PASSPHRASE),
            MAIL_PASSWORD)

    def test_without_the_passphrase_nobody_can_read_it(self):
        mailbox = _account(self.anna)
        keyring.store_password(mailbox, MAIL_PASSWORD, passphrase=PASSPHRASE)

        with self.assertRaises(keyring.MailboxLocked):
            keyring.read_password(mailbox, passphrase="")
        with self.assertRaises(keyring.MailboxLocked):
            keyring.read_password(mailbox, passphrase="not the passphrase")

    def test_connecting_provisions_a_strongbox_of_its_own(self):
        # Nothing on this platform makes a personal strongbox at signup, so
        # connecting a mailbox is where one comes into being — and it is a
        # box of its own, not the wallet-PIN or signing box.
        from toto.gervazy.models import UserStrongbox

        self.assertFalse(UserStrongbox.objects.filter(owner=self.anna).exists())
        mailbox = _account(self.anna)
        keyring.store_password(mailbox, MAIL_PASSWORD, passphrase=PASSPHRASE)

        boxes = UserStrongbox.objects.filter(owner=self.anna)
        self.assertEqual([b.name for b in boxes],
                         [keyring.PERSONAL_STRONGBOX_NAME])

    def test_one_persons_passphrase_does_not_open_anothers_mailbox(self):
        hers = _account(self.anna)
        his = _account(self.bob)
        keyring.store_password(hers, MAIL_PASSWORD, passphrase=PASSPHRASE)
        keyring.store_password(his, "his-own-password", passphrase="his own")

        with self.assertRaises(keyring.MailboxLocked):
            keyring.read_password(his, passphrase=PASSPHRASE)

    def test_replacing_a_password_retires_the_old_secret(self):
        mailbox = _account(self.anna)
        first = keyring.store_password(mailbox, MAIL_PASSWORD,
                                       passphrase=PASSPHRASE)
        second = keyring.store_password(mailbox, "rotated",
                                        passphrase=PASSPHRASE)
        first.refresh_from_db()

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.state, "retired")
        self.assertEqual(keyring.read_password(mailbox, passphrase=PASSPHRASE),
                         "rotated")


class MailboxShapeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="x")

    def test_starttls_and_implicit_tls_together_are_refused(self):
        mailbox = _account(self.anna, use_tls=True, use_ssl=True)
        with self.assertRaises(ValidationError):
            mailbox.clean()

    def test_a_personal_mailbox_needs_an_owner(self):
        orphan = Mailbox(kind=MailboxKind.PERSONAL, label="x",
                         email_address="x@example.org")
        with self.assertRaises(ValidationError):
            orphan.clean()

    def test_the_system_mailbox_belongs_to_nobody_and_to_the_platform_box(self):
        owned = Mailbox(kind=MailboxKind.SYSTEM, owner=self.anna, label="s",
                        email_address="s@example.org",
                        keyholder=Keyholder.PLATFORM)
        with self.assertRaises(ValidationError):
            owned.clean()

        personally_sealed = Mailbox(kind=MailboxKind.SYSTEM, label="s",
                                    email_address="s@example.org",
                                    keyholder=Keyholder.PERSON)
        with self.assertRaises(ValidationError):
            personally_sealed.clean()

    def test_designating_a_second_system_mailbox_demotes_the_first(self):
        first = Mailbox.objects.create(
            kind=MailboxKind.SYSTEM, label="Old platform",
            email_address="old@example.org", keyholder=Keyholder.PLATFORM)
        second = Mailbox.objects.create(
            kind=MailboxKind.SYSTEM, label="New platform",
            email_address="new@example.org", keyholder=Keyholder.PLATFORM)

        first.refresh_from_db()
        self.assertEqual(first.kind, MailboxKind.PERSONAL)
        self.assertFalse(first.is_active)
        self.assertEqual(
            Mailbox.objects.filter(kind=MailboxKind.SYSTEM).count(), 1)
        self.assertEqual(guardian.system_mailbox().pk, second.pk)

    def test_capability_questions(self):
        mailbox = _account(self.anna)
        self.assertFalse(mailbox.can_send)      # no password yet
        keyring.store_password(mailbox, MAIL_PASSWORD, passphrase=PASSPHRASE)
        mailbox.refresh_from_db()
        self.assertTrue(mailbox.can_send)
        self.assertTrue(mailbox.can_receive)

        send_only = _account(self.anna, email_address="send@example.org",
                             imap_host="")
        keyring.store_password(send_only, MAIL_PASSWORD, passphrase=PASSPHRASE)
        send_only.refresh_from_db()
        self.assertTrue(send_only.can_send)
        self.assertFalse(send_only.can_receive)


class GuardianTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("gwen", password="x")
        cls.person = Person.objects.create(user=cls.user, display_name="Gwen")
        cls.other = User.objects.create_user("owen", password="x")

    def _office(self, holder=None):
        return Station.objects.create(
            name=guardian.GUARDIAN_NAME, slug=guardian.GUARDIAN_SLUG,
            holder=holder, active=True)

    def _system_mailbox(self):
        mailbox = Mailbox.objects.create(
            kind=MailboxKind.SYSTEM, label="Platform",
            email_address="platform@example.org",
            host="smtp.example.org", username="platform",
            keyholder=Keyholder.PLATFORM)
        # Stand in for the platform strongbox: the point of this test is the
        # office logic, not jess's Argon2id envelope (covered in jess.tests).
        from toto.gervazy.models import EncryptedSecret
        mailbox.secret = EncryptedSecret.objects.first()
        return mailbox

    def test_no_office_no_platform_mail(self):
        with self.assertRaises(guardian.NoSystemMailbox):
            guardian.platform_sender()
        self.assertIn("no system mailbox", guardian.describe().lower())

    def test_a_vacant_office_refuses_rather_than_falling_back(self):
        self._office(holder=None)
        mailbox = self._system_mailbox()
        Mailbox.objects.filter(pk=mailbox.pk).update(secret=None)

        with self.assertRaises(guardian.NoSystemMailbox):
            guardian.platform_sender()

    def test_is_guardian_follows_the_office_not_the_person(self):
        station = self._office(holder=self.person)
        self.assertTrue(guardian.is_guardian(self.user))
        self.assertFalse(guardian.is_guardian(self.other))

        # Handover: the mailbox row is untouched, access moves.
        successor = Person.objects.create(user=self.other,
                                          display_name="Owen")
        station.holder = successor
        station.save(update_fields=["holder"])

        self.assertFalse(guardian.is_guardian(self.user))
        self.assertTrue(guardian.is_guardian(self.other))

    def test_a_superuser_is_not_automatically_the_guardian(self):
        root = User.objects.create_superuser("root", "r@x.com", "x")
        self._office(holder=self.person)
        self.assertFalse(guardian.is_guardian(root))

    def test_an_anonymous_visitor_is_never_the_guardian(self):
        from django.contrib.auth.models import AnonymousUser

        self._office(holder=self.person)
        self.assertFalse(guardian.is_guardian(AnonymousUser()))


class SentRecordTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="x")

    def test_one_row_per_recipient_is_what_makes_partial_sends_honest(self):
        mailbox = _account(self.anna)
        for address, status, error in (
                ("a@example.org", "sent", ""),
                ("b@example.org", "sent", ""),
                ("c@example.org", "failed", "550 mailbox unavailable")):
            SentRecord.objects.create(
                mailbox=mailbox, message_id="<m1@example.org>",
                recipient=address, subject="Notice", status=status,
                error=error, sent_by=self.anna)

        rows = SentRecord.objects.filter(message_id="<m1@example.org>")
        self.assertEqual(rows.count(), 3)
        self.assertEqual(rows.filter(status="failed").get().recipient,
                         "c@example.org")
        # The campaign runway: grouping exists, unused today.
        self.assertEqual(rows.exclude(campaign_ref="").count(), 0)
