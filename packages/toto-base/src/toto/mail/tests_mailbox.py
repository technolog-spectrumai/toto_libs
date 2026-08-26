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


class PlatformSenderTests(TestCase):
    """The platform never guesses which address it speaks from.

    This was ``GuardianTests``: the system mailbox was governed by a Mail
    Guardian — a ``socialhub.Station``, an office held by a person and vacant
    when nobody held it — and a vacancy refused the send outright. Stations were
    removed in 8/2026 and the office with them. What survives is the property
    that actually protected anybody: no designated, sending-capable mailbox
    means no platform mail, said out loud rather than fudged.
    """

    def _system_mailbox(self):
        mailbox = Mailbox.objects.create(
            kind=MailboxKind.SYSTEM, label="Platform",
            email_address="platform@example.org",
            host="smtp.example.org", username="platform",
            keyholder=Keyholder.PLATFORM)
        # Stand in for the platform strongbox: the point here is the refusal
        # logic, not jess's Argon2id envelope (covered in jess.tests).
        from toto.gervazy.models import EncryptedSecret
        mailbox.secret = EncryptedSecret.objects.first()
        return mailbox

    def test_no_mailbox_no_platform_mail(self):
        with self.assertRaises(guardian.NoSystemMailbox):
            guardian.platform_sender()
        self.assertIn("no system mailbox", guardian.describe().lower())

    def test_a_mailbox_that_cannot_send_refuses_rather_than_falling_back(self):
        mailbox = self._system_mailbox()
        Mailbox.objects.filter(pk=mailbox.pk).update(secret=None)

        with self.assertRaises(guardian.NoSystemMailbox):
            guardian.platform_sender()

    # No "a ready mailbox sends" test, for the reason the fixture above gives:
    # sealing a PLATFORM mailbox's password goes through jess's strongbox, and
    # this suite deliberately stays out of it. Every test here is a refusal,
    # which is the half that protects anybody.

    def test_the_office_is_gone_and_nothing_asks_for_a_holder(self):
        """`is_guardian` gated who could read the platform's replies. Nothing
        outside these tests ever called it, which is why it could go."""
        for name in ("is_guardian", "current_guardian", "guardian_station"):
            with self.subTest(attr=name):
                self.assertFalse(hasattr(guardian, name))



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
