"""Mana per kilobyte: the arithmetic, the estimate, and when a post is not
charged (stage 69).

The owner, 2026-10-07: "Charge a tiny configurable amount per KB of UTF-8
message text; image KB cost more. Use existing mana/tariffs, precise
arithmetic and explicit rounding. Show the estimated charge before posting.
Refuse insufficient balances; prevent duplicate charges on retries and
charges for failed posts."

Run against a real ledger: three full pools and the seeded rate card, as
any ingress leaves a platform.

    manage.py test toto.forum.tests.test_billing
"""

import inspect
import json
import re
from decimal import Decimal
from unittest import mock

from django.db import transaction
from django.test import SimpleTestCase, override_settings

from toto.forum import billing, images, metrics, posting
from toto.forum.models import ForumMessage, ForumQuotaPolicy, ForumSettings, ForumUsageEvent
from toto.forum.testing import PNG, ForumCase, client_of, op, send_json, upload
from toto.mana.tests.fixtures import MASTER, held, seed_prices, spend
from toto.tariffs.models import TariffItem, UsageCharge, UsageRecord
from toto.vault.models import VaultFile

#: A unit of storage mana in the ledger's smallest units (nine decimals).
SCALE = 10 ** 9
TEXT_PRICE = Decimal("0.001")
IMAGE_PRICE = Decimal("0.002")


def up(size: int, price: Decimal) -> Decimal:
    """``size`` bytes at ``price`` a kilobyte, rounded up to the smallest
    unit — in whole numbers only, so it shares no code with what it checks."""
    base = int(price * SCALE)
    return Decimal(-(-size * base // 1024)) / SCALE


def picture(size: int) -> bytes:
    """A PNG by its first bytes, exactly ``size`` bytes long."""
    return PNG + b"\x00" * (size - len(PNG))


class QuantityTests(SimpleTestCase):
    def test_a_kilobyte_is_1024_bytes_exactly(self):
        self.assertEqual(billing.kilobytes(0), Decimal(0))
        self.assertEqual(billing.kilobytes(1), Decimal("0.0009765625"))
        self.assertEqual(billing.kilobytes(1023), Decimal("0.9990234375"))
        self.assertEqual(billing.kilobytes(1024), Decimal(1))
        self.assertEqual(billing.kilobytes(1025), Decimal("1.0009765625"))
        self.assertEqual(billing.kilobytes(10 * 1024 * 1024), Decimal(10240))
        self.assertEqual(billing.kilobytes(3_000_000), Decimal("2929.6875"))

    def test_every_size_survives_the_ledger_s_ten_decimal_places(self):
        places = Decimal(1).scaleb(-10)
        for size in (1, 7, 1023, 1025, 8191, 8192, 10 * 1024 * 1024 - 1):
            with self.subTest(size=size):
                quantity = billing.kilobytes(size)
                self.assertIsInstance(quantity, Decimal)
                self.assertEqual(quantity.quantize(places), quantity)
                self.assertEqual(quantity * 1024, size)

    def test_a_size_is_a_whole_number_of_bytes(self):
        for bad in (-1, 1.5, True, "12", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                billing.kilobytes(bad)

    def test_only_what_there_is_is_billed_text_first(self):
        self.assertEqual(billing.lines(0, 0), [])
        self.assertEqual([line.code for line in billing.lines(5, 0)], [metrics.TEXT_KB])
        self.assertEqual([line.code for line in billing.lines(0, 5)], [metrics.IMAGE_KB])
        both = billing.lines(1024, 2048)
        self.assertEqual([(line.code, line.size, line.quantity, line.unit) for line in both],
                         [(metrics.TEXT_KB, 1024, Decimal(1), "kb"),
                          (metrics.IMAGE_KB, 2048, Decimal(2), "kb")])

    def test_no_float_is_made_on_the_way(self):
        for module in (billing, metrics):
            source = inspect.getsource(module)
            self.assertIsNone(re.search(r"\bfloat\s*\(", source), module.__name__)
            self.assertNotIn("round(", source)

    def test_decimal_strings_are_plain(self):
        self.assertEqual(billing.text_of(Decimal("0E-9")), "0")
        self.assertEqual(billing.text_of(Decimal("0.000000977")), "0.000000977")
        self.assertEqual(billing.text_of(Decimal("100.000000000")), "100")
        self.assertEqual(billing.text_of(Decimal("5.859375000")), "5.859375")


@override_settings(**MASTER)
class BilledCase(ForumCase):
    """The Guild on a platform with its three pools and the seeded prices;
    everybody starts with a full pool of storage mana (100)."""

    @classmethod
    def setUpTestData(cls):
        from toto.assets.services.bootstrap import bootstrap_economy
        from toto.core.models import Platform
        from toto.mana import services

        Platform.objects.get_or_create(
            site_name="T", defaults={"author": "A", "publication_year": 2026, "active": True})
        bootstrap_economy()
        seed_prices()
        super().setUpTestData()
        for user in (cls.member, cls.second, cls.head, cls.admin):
            services.fill_pools(user)
        # Every post is charged in these classes (the threshold at 0), so
        # that the charging rules are the ones under test; FreeBelowTests
        # sets a threshold of its own.
        ForumSettings.objects.update_or_create(pk=1, defaults={"free_below_kb": cls.FREE_BELOW_KB})

    #: The forum's free threshold in this class, in kilobytes.
    FREE_BELOW_KB = 0

    def held(self, user=None) -> Decimal:
        return held(user or self.member, "storage")

    def leave(self, amount, user=None):
        """Spend the pool down to ``amount``."""
        user = user or self.member
        spend(user, "storage", self.held(user) - Decimal(amount))

    def estimate(self, user=None, **sizes):
        return client_of(user or self.member).post(self.url("estimate"), sizes)

    def nothing_was_kept(self):
        self.assertFalse(ForumMessage.objects.exists())
        self.assertFalse(VaultFile.objects.exists())
        self.assertFalse(ForumUsageEvent.objects.exists())
        self.assertFalse(UsageRecord.objects.filter(metric_code__startswith="forum.").exists())


class PriceTests(BilledCase):
    def test_the_two_rows_of_the_rate_card(self):
        from toto.mana import services

        text = TariffItem.objects.get(metric__code=metrics.TEXT_KB)
        image = TariffItem.objects.get(metric__code=metrics.IMAGE_KB)
        storage = services.pools()["storage"].asset
        for item, price in ((text, TEXT_PRICE), (image, IMAGE_PRICE)):
            with self.subTest(item=item.metric.code):
                self.assertEqual(item.price_per_unit_display, price)
                self.assertEqual(item.charged_asset, storage)
                self.assertEqual(item.unit.code, "kb")
                self.assertEqual(item.unit_quantity, 1)
                # The rounding is written on the row: up, to a whole base unit.
                self.assertEqual(item.rounding_mode, "up")
                self.assertEqual(item.minimum_charge_base_units, 0)
        self.assertGreater(image.price_per_unit_base_units, text.price_per_unit_base_units)
        self.assertEqual(storage.decimals, 9)
        self.assertEqual(billing.prices(), {"text_kb": "0.001", "image_kb": "0.002"})

    def test_a_host_setting_overrides_a_seed(self):
        from toto.mana import services

        with override_settings(MANA_PRICES={"forum.text_kb": "0.004", "forum.image_kb": None}):
            seeds = services.seed_prices()
        self.assertEqual(seeds["forum.text_kb"], Decimal("0.004"))
        self.assertNotIn("forum.image_kb", seeds)

    def test_everybody_starts_with_a_full_pool(self):
        self.assertEqual(self.held(), Decimal(100))


class ArithmeticTests(BilledCase):
    """quantity × price, rounded once, up, to the ledger's smallest unit."""

    SIZES = (0, 1, 1023, 1024, 1025, 8192, 3_000_000, 10 * 1024 * 1024)

    def test_text_at_the_edges(self):
        for size in self.SIZES:
            with self.subTest(size=size):
                quote = billing.quote(self.member, text_bytes=size, image_bytes=0)
                self.assertEqual(quote.text, up(size, TEXT_PRICE))
                self.assertEqual(quote.image, 0)
                self.assertEqual(quote.amount, quote.text)

    def test_an_image_at_the_edges(self):
        for size in self.SIZES:
            with self.subTest(size=size):
                quote = billing.quote(self.member, text_bytes=0, image_bytes=size)
                self.assertEqual(quote.image, up(size, IMAGE_PRICE))
                self.assertEqual(quote.text, 0)

    def test_the_worked_examples(self):
        one = billing.quote(self.member, text_bytes=1, image_bytes=0)
        # 1/1024 KB × 0.001 = 0.0000009765625: up to nine places.
        self.assertEqual(one.text, Decimal("0.000000977"))
        whole = billing.quote(self.member, text_bytes=1024, image_bytes=0)
        self.assertEqual(whole.text, Decimal("0.001"))          # exact: nothing to round
        over = billing.quote(self.member, text_bytes=1025, image_bytes=0)
        self.assertEqual(over.text, Decimal("0.001000977"))     # 0.0010009765625, up
        big = billing.quote(self.member, text_bytes=0, image_bytes=3_000_000)
        self.assertEqual(big.image, Decimal("5.859375"))        # 2929.6875 KB × 0.002
        most = billing.quote(self.member, text_bytes=0, image_bytes=10 * 1024 * 1024)
        self.assertEqual(most.image, Decimal("20.48"))

    def test_the_rounding_is_up_and_never_down(self):
        for size in range(1, 60):
            with self.subTest(size=size):
                exact = Decimal(size) / 1024 * TEXT_PRICE
                charged = billing.quote(self.member, text_bytes=size, image_bytes=0).text
                self.assertGreaterEqual(charged, exact)
                self.assertLess(charged - exact, Decimal(1) / SCALE)

    def test_a_post_of_both_is_the_sum_of_two_roundings(self):
        quote = billing.quote(self.member, text_bytes=1, image_bytes=1)
        self.assertEqual(quote.text, Decimal("0.000000977"))
        self.assertEqual(quote.image, Decimal("0.000001954"))   # 0.000001953125, up
        self.assertEqual(quote.amount, Decimal("0.000002931"))

    def test_an_image_kilobyte_costs_more_than_a_text_kilobyte(self):
        quote = billing.quote(self.member, text_bytes=4096, image_bytes=4096)
        self.assertGreater(quote.image, quote.text)


class EstimateTests(BilledCase):
    def test_the_door_answers_the_contract(self):
        response = self.estimate(text_bytes=1025, image_bytes=3_000_000)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.json(), {
            "amount": "5.860375977", "text": "0.001000977", "image": "5.859375",
            "affordable": True, "balance": "100", "display": "5.86 storage mana",
            "free": False})

    def test_json_and_a_form_are_the_same_question(self):
        sizes = {"text_bytes": 300, "image_bytes": 70_000}
        form = self.estimate(**sizes).json()
        sent = send_json(client_of(self.member), self.url("estimate"), sizes).json()
        self.assertEqual(form, sent)
        self.assertEqual(form["text"], str(up(300, TEXT_PRICE)))

    def test_nothing_is_stored_and_nothing_is_charged(self):
        before = self.held()
        for _ in range(3):
            self.assertEqual(self.estimate(text_bytes=8000, image_bytes=5_000_000).status_code, 200)
        self.assertEqual(self.held(), before)
        self.nothing_was_kept()
        self.assertFalse(UsageCharge.objects.exists())

    def test_nothing_to_send_costs_nothing(self):
        self.assertEqual(self.estimate().json(), {
            "amount": "0", "text": "0", "image": "0", "affordable": True,
            "balance": "100", "display": "0 storage mana", "free": False})

    def test_it_says_when_the_pool_is_short(self):
        self.leave("0.5")
        answer = self.estimate(image_bytes=1024 * 1024).json()       # 2.048
        self.assertEqual((answer["amount"], answer["affordable"], answer["balance"]),
                         ("2.048", False, "0.5"))
        self.assertTrue(self.estimate(text_bytes=100).json()["affordable"])

    def test_bad_input_is_400_with_a_sentence(self):
        client, url = client_of(self.member), self.url("estimate")
        for bad in ({"text_bytes": -1}, {"text_bytes": 1.5}, {"image_bytes": True},
                    {"text_bytes": "many"}, {"image_bytes": "1e3"}, {"text_bytes": [1]},
                    {"text_bytes": posting.MAX_TEXT_BYTES + 1},
                    {"image_bytes": images.MAX_BYTES + 1}):
            with self.subTest(bad=bad):
                response = send_json(client, url, bad)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.json()["error"])
        self.assertEqual(client.post(url, {"text_bytes": "-3"}).status_code, 400)
        self.assertEqual(client.post(url, {"text_bytes": "12abc"}).status_code, 400)
        self.assertEqual(client.post(url, data="[1]", content_type="application/json")
                         .status_code, 400)
        # The greatest sizes a post may have are asked about, not refused.
        self.assertEqual(send_json(client, url, {
            "text_bytes": posting.MAX_TEXT_BYTES, "image_bytes": images.MAX_BYTES}).status_code,
            200)

    def test_who_may_ask(self):
        url = self.url("estimate")
        self.assertEqual(client_of(self.member).get(url).status_code, 405)
        # Signed out: the door's own 403, or the host's sign-in redirect first.
        self.assertIn(self.client.post(url, {"text_bytes": 1}).status_code, (302, 401, 403))
        self.assertEqual(client_of(self.free).post(url, {"text_bytes": 1}).status_code, 402)
        self.assertEqual(client_of(self.outsider).post(url, {"text_bytes": 1}).status_code, 403)
        self.assertEqual(client_of(self.staff).post(url, {"text_bytes": 1}).status_code, 403)
        for user in (self.member, self.senior, self.head, self.admin):
            with self.subTest(user=user.username):
                self.assertEqual(client_of(user).post(url, {"text_bytes": 1}).status_code, 200)

    def test_the_page_is_given_the_door_and_the_prices(self):
        html = client_of(self.member).get(self.url("channel_detail")).content.decode()
        block = re.search(r'<script id="forum-channel-config" type="application/json">(.*?)'
                          r"</script>", html, re.S)
        config = json.loads(block.group(1))
        self.assertEqual(config["urls"]["estimate"], "/forum/guild/estimate/")
        self.assertEqual(config["prices"],
                         {"text_kb": "0.001", "image_kb": "0.002", "free_below_kb": 0})


class ChargeTests(BilledCase):
    def test_the_estimate_equals_the_charge(self):
        """The same sizes asked first and then posted: what the door said is
        what left the pool, to the last unit."""
        cases = (("x", None), ("z" * 1023, None), ("ż" * 600, None),      # 1, 1023, 1200 bytes
                 ("", 1025), ("with a picture", 70_001), ("", 2_500_001))
        for text, size in cases:
            with self.subTest(text=len(text), image=size):
                sizes = {"text_bytes": len(text.encode()), "image_bytes": size or 0}
                said = Decimal(self.estimate(**sizes).json()["amount"])
                before = self.held()
                image = upload(picture(size)) if size else None
                response = self.say(self.member, text, image=image)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(before - self.held(), said)
                row = ForumMessage.objects.get(pk=response.json()["message"]["id"])
                posted = sum(charge.amount_base_units for charge in UsageCharge.objects.filter(
                    usage_record__source_id=str(row.id)))
                self.assertEqual(Decimal(posted) / SCALE, said)
                self.assertGreater(said, 0)

    def test_the_estimate_equals_the_charge_under_a_discount(self):
        """A member's community discount is the ledger's, taken off after
        the rounding; the estimate is the same calculator, so it shows it."""
        sizes = {"text_bytes": 1025, "image_bytes": 0}
        listed = Decimal(self.estimate(**sizes).json()["amount"])
        with mock.patch("toto.tariffs.discounts.percent_for_account",
                        return_value=(25, "Guild")):
            said = Decimal(self.estimate(**sizes).json()["amount"])
            before = self.held()
            self.assertEqual(self.say(self.member, "x" * 1025).status_code, 201)
        self.assertEqual(before - self.held(), said)
        self.assertEqual(listed, Decimal("0.001000977"))
        self.assertEqual(said, Decimal("0.000750732"))      # 1000977 × 75 // 100 units

    def test_one_event_and_one_charge_for_each_metric(self):
        response = self.say(self.member, "caption", image=upload(picture(4096)))
        row = ForumMessage.objects.get(pk=response.json()["message"]["id"])
        events = {event.metric_code: event for event in ForumUsageEvent.objects.all()}
        self.assertEqual(set(events), {"forum.text_kb", "forum.image_kb"})
        self.assertEqual(events["forum.text_kb"].quantity, Decimal(7) / 1024)
        self.assertEqual(events["forum.image_kb"].quantity, Decimal(4))
        for code, event in events.items():
            self.assertEqual(event.idempotency_key, f"{code}:{row.id}")
            self.assertEqual((event.source_type, event.source_id, event.unit, event.user),
                             ("forum.ForumMessage", str(row.id), "kb", self.member))
        records = UsageRecord.objects.filter(source_id=str(row.id))
        self.assertEqual(sorted(records.values_list("metric_code", flat=True)),
                         ["forum.image_kb", "forum.text_kb"])
        self.assertEqual(self.held(), 100 - up(7, TEXT_PRICE) - Decimal("0.008"))

    def test_nothing_of_the_message_is_in_an_event_or_a_charge(self):
        secret = "the harbour at dawn"
        self.say(self.member, secret, image=upload(picture(2000), name="harbour-secret.png"))
        kept = []
        for event in ForumUsageEvent.objects.all():
            self.assertEqual(set(event.metadata), {"bytes"})
            kept += [event.source_label, json.dumps(event.metadata)]
        for record in UsageRecord.objects.all():
            kept += [json.dumps(record.metadata), record.source_type]
            if record.ledger_transaction is not None:
                kept += [record.ledger_transaction.description,
                         json.dumps(record.ledger_transaction.metadata)]
        text = " ".join(kept)
        self.assertNotIn("harbour", text)
        self.assertNotIn("dawn", text)
        self.assertIn("Forum message: text", text)

    def test_a_retry_is_charged_once(self):
        the_op = op()
        first = self.say(self.member, "once", the_op=the_op)
        after = self.held()
        again = self.say(self.member, "once", the_op=the_op)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertTrue(again.json()["replay"])
        self.assertEqual(again.json()["message"]["id"], first.json()["message"]["id"])
        self.assertEqual(self.held(), after)
        self.assertEqual(100 - after, up(4, TEXT_PRICE))
        self.assertEqual(ForumUsageEvent.objects.count(), 1)
        self.assertEqual(UsageRecord.objects.filter(metric_code="forum.text_kb").count(), 1)
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_a_retry_is_answered_even_with_an_empty_pool(self):
        the_op = op()
        self.say(self.member, "once", the_op=the_op)
        self.leave(0)
        again = self.say(self.member, "once", the_op=the_op)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(self.held(), 0)

    def test_a_second_settle_of_one_message_charges_nothing(self):
        response = self.say(self.member, "caption", image=upload(picture(4096)))
        row = ForumMessage.objects.get(pk=response.json()["message"]["id"])
        after = self.held()
        with transaction.atomic():
            billing.settle_post(self.member, row, text_bytes=7, image_bytes=4096)
        self.assertEqual(self.held(), after)
        self.assertEqual(ForumUsageEvent.objects.count(), 2)
        self.assertEqual(UsageRecord.objects.filter(source_id=str(row.id)).count(), 2)

    def test_an_empty_pool_refuses_and_nothing_is_stored(self):
        self.leave("0.5")
        with mock.patch.object(images, "store", wraps=images.store) as stored:
            response = self.say(self.member, "a picture", image=upload(picture(1024 * 1024)))
        self.assertEqual(response.status_code, 402)
        sentence = response.json()["error"]
        self.assertIn("storage mana", sentence)
        self.assertIn("you have 0.5", sentence)
        stored.assert_not_called()                 # no image was written
        self.nothing_was_kept()
        self.assertEqual(self.held(), Decimal("0.5"))

    def test_a_tiny_shortfall_is_said_in_figures_not_as_nought(self):
        self.leave(0)
        sentence = self.say(self.member, "x").json()["error"]
        self.assertIn("this needs 0.000000977 and you have 0", sentence)

    def test_the_text_and_the_image_are_weighed_together(self):
        """Enough for either alone, short of both: refused before anything
        is stored, for the sum."""
        text, size = "y" * 8000, 4096                    # 0.0078125 and 0.008
        self.leave("0.01")
        self.assertEqual(self.say(self.member, text, image=upload(picture(size))).status_code, 402)
        self.nothing_was_kept()
        self.assertEqual(self.say(self.member, text).status_code, 201)

    def test_a_post_that_fails_after_the_charge_is_rolled_back_with_it(self):
        from toto.quota.charge import charge as real_charge

        calls = []

        def charged_then_broken(*args, **kwargs):
            calls.append(real_charge(*args, **kwargs))
            raise RuntimeError("the server fell over after the ledger posted")

        with mock.patch.object(billing, "charge", side_effect=charged_then_broken), \
                mock.patch.object(images, "discard", wraps=images.discard) as discarded:
            with self.assertRaises(RuntimeError):
                posting.post_message(self.member, self.channel, text="lost", op=op(),
                                     image=(picture(3000), "image/png"))
        self.assertEqual(len(calls), 1)             # the ledger did post, inside the block
        discarded.assert_called_once()              # the image's bytes were unlinked
        self.nothing_was_kept()
        self.assertEqual(self.held(), Decimal(100))

    def test_a_post_that_fails_before_the_charge_is_never_charged(self):
        with mock.patch.object(images, "store", side_effect=OSError("disk full")), \
                mock.patch.object(billing, "charge") as charged:
            with self.assertRaises(OSError):
                posting.post_message(self.member, self.channel, text="lost", op=op(),
                                     image=(picture(3000), "image/png"))
        charged.assert_not_called()
        self.nothing_was_kept()
        self.assertEqual(self.held(), Decimal(100))

    def test_a_pool_emptied_between_the_check_and_the_charge(self):
        """Another request spent the mana after this one was let through:
        the ledger refuses inside the transaction, the message and its image
        go with it, and the door says so in the pool's own sentence."""
        self.leave("0.001")
        real = billing.check_funds_all
        passes = iter([None])

        def first_passes(*args, **kwargs):
            try:
                return next(passes)
            except StopIteration:
                return real(*args, **kwargs)

        with mock.patch.object(billing, "check_funds_all", side_effect=first_passes), \
                mock.patch.object(images, "discard", wraps=images.discard) as discarded:
            response = self.say(self.member, "a picture", image=upload(picture(50_000)))
        self.assertEqual(response.status_code, 402)
        self.assertIn("storage mana", response.json()["error"])
        discarded.assert_called_once()
        self.nothing_was_kept()
        self.assertEqual(self.held(), Decimal("0.001"))

    def test_the_day_s_cap_refuses_with_429(self):
        ForumQuotaPolicy.objects.create(metric_code="forum.text_kb", limit=Decimal("0.01"),
                                        unit="kb")
        self.assertEqual(self.say(self.member, "ten bytes!").status_code, 201)
        response = self.say(self.member, "x" * 100)
        self.assertEqual(response.status_code, 429)
        self.assertTrue(response.json()["error"])
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_nobody_is_exempt(self):
        """The platform exempts nobody from a mana charge and the forum adds
        no exemption: the head and an administrator pay as a member does."""
        for user in (self.head, self.admin):
            with self.subTest(user=user.username):
                self.assertEqual(self.say(user, "x" * 2048).status_code, 201)
                self.assertEqual(100 - self.held(user), Decimal("0.002"))

    def test_removing_and_polls_cost_nothing(self):
        message = self.say(self.member, "x" * 1024).json()["message"]
        after = self.held()
        self.assertEqual(after, Decimal("99.999"))
        client_of(self.member).post(self.url("message_remove", message["id"]))
        self.open_poll(self.member)
        self.assertEqual(self.held(), after)
        self.assertEqual(ForumUsageEvent.objects.count(), 1)


class FreeBelowTests(BilledCase):
    """The owner, 2026-10-07: "make forum messages free below threshold
    (like 300kB) - make this setting param". A post whose text and picture
    together are smaller than the forum's threshold costs nothing; at the
    threshold and above it is charged for its whole size, as before."""

    FREE_BELOW_KB = 2           # 2048 bytes: small enough to stand on both sides of

    def charges(self):
        return UsageCharge.objects.filter(usage_record__metric_code__startswith="forum.")

    def test_the_dial_arrives_at_300(self):
        self.assertEqual(ForumSettings._meta.get_field("free_below_kb").default, 300)
        self.assertEqual(billing.free_below_bytes(), 2048)

    def test_a_small_message_costs_nothing_and_is_still_counted(self):
        before = self.held()
        response = self.say(self.member, "hello")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.held(), before)
        self.assertFalse(self.charges().exists())
        # What the day's caps count is written, with its quantity.
        (event,) = ForumUsageEvent.objects.all()
        self.assertEqual((event.metric_code, event.quantity), (metrics.TEXT_KB, billing.kilobytes(5)))

    def test_one_byte_under_is_free_and_the_threshold_itself_is_charged_whole(self):
        before = self.held()
        self.assertEqual(self.say(self.member, "x" * 2047).status_code, 201)
        self.assertEqual(self.held(), before)
        self.assertEqual(self.say(self.member, "y" * 2048).status_code, 201)
        self.assertEqual(before - self.held(), up(2048, TEXT_PRICE))

    def test_text_and_picture_are_weighed_together(self):
        before = self.held()
        # 800 + 1100 = 1900 bytes: free, though the picture is the larger part.
        self.assertEqual(self.say(self.member, "a" * 800, image=upload(picture(1100))).status_code, 201)
        self.assertEqual(self.held(), before)
        # 1000 + 1100 = 2100 bytes: each alone is under the threshold, the post is not.
        self.assertEqual(self.say(self.member, "b" * 1000, image=upload(picture(1100))).status_code, 201)
        self.assertEqual(before - self.held(), up(1000, TEXT_PRICE) + up(1100, IMAGE_PRICE))

    def test_an_empty_pool_posts_a_free_message_and_is_refused_a_charged_one(self):
        self.leave(0)
        self.assertEqual(self.say(self.member, "still here").status_code, 201)
        refused = self.say(self.member, "z" * 4000)
        self.assertEqual(refused.status_code, 402)
        self.assertEqual(ForumMessage.objects.count(), 1)

    def test_the_estimate_says_free_and_shows_no_cost(self):
        self.leave(0)
        free = self.estimate(text_bytes=100, image_bytes=1000).json()
        self.assertEqual((free["free"], free["amount"], free["text"], free["image"], free["display"],
                          free["affordable"]), (True, "0", "0", "0", "", True))
        charged = self.estimate(text_bytes=100, image_bytes=2000).json()
        self.assertFalse(charged["free"])
        self.assertFalse(charged["affordable"])
        self.assertGreater(Decimal(charged["amount"]), 0)

    def test_a_retry_of_a_free_post_answers_the_stored_message(self):
        the_op = op()
        first = self.say(self.member, "once", the_op=the_op)
        again = self.say(self.member, "once", the_op=the_op)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertTrue(again.json()["replay"])
        self.assertEqual(ForumMessage.objects.count(), 1)
        self.assertEqual(ForumUsageEvent.objects.count(), 1)
        self.assertFalse(self.charges().exists())

    def test_the_day_s_cap_still_refuses_a_free_post(self):
        ForumQuotaPolicy.objects.create(metric_code="forum.text_kb", limit=Decimal("0.01"),
                                        unit="kb")
        self.assertEqual(self.say(self.member, "ten bytes!").status_code, 201)
        self.assertEqual(self.say(self.member, "x" * 100).status_code, 429)
        self.assertEqual(ForumMessage.objects.count(), 1)
        self.assertFalse(self.charges().exists())

    def test_the_dial_is_read_when_the_post_is_made(self):
        ForumSettings.objects.filter(pk=1).update(free_below_kb=0)
        before = self.held()
        self.assertEqual(self.say(self.member, "hello").status_code, 201)
        self.assertEqual(before - self.held(), up(5, TEXT_PRICE))
        ForumSettings.objects.filter(pk=1).update(free_below_kb=300)
        before = self.held()
        self.assertEqual(self.say(self.member, "w" * 8000, image=upload(picture(200_000))).status_code, 201)
        self.assertEqual(self.held(), before)


class UnpricedTests(BilledCase):
    def test_without_a_price_a_post_is_free_and_still_counted(self):
        TariffItem.objects.filter(metric__code__startswith="forum.").delete()
        self.assertEqual(self.estimate(text_bytes=500, image_bytes=5000).json(), {
            "amount": "0", "text": "0", "image": "0", "affordable": True,
            "balance": None, "display": "", "free": False})
        self.assertEqual(billing.prices(), {"text_kb": "0", "image_kb": "0"})
        self.leave(0)
        self.assertEqual(self.say(self.member, "free").status_code, 201)
        self.assertEqual(ForumUsageEvent.objects.count(), 1)

    def test_one_price_alone_is_charged_alone(self):
        TariffItem.objects.filter(metric__code="forum.text_kb").delete()
        answer = self.estimate(text_bytes=2048, image_bytes=2048).json()
        self.assertEqual((answer["text"], answer["image"], answer["amount"]),
                         ("0", "0.004", "0.004"))
        self.assertEqual(answer["balance"], "100")
