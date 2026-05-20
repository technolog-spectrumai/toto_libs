from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.utils import timezone

from toto.assets.models import AccountType, Asset, Currency, LedgerAccount, LedgerTransaction
from toto.assets.services.assets import create_asset, transfer_asset
from toto.bazaar.models import MarketCustodian, Shop
from toto.ingress import IngressCommand
from toto.locations.models import Address, Territory
from toto.people.models import Person

from ...models import (
    Bounty,
    BountyBoard,
    BountyClaim,
    BountyPayment,
    BountySubmission,
    Detection,
    DetectionCategory,
    DetectionHandle,
)


class Command(IngressCommand):
    help = "Seed demo detections, map points, bounty boards, and asset-backed bounty payments."

    def process(self):
        self.stdout.write("Seeding detections and bounties...")
        accounts, asset, currency = self._ensure_payment_asset()
        people = self._ensure_people()
        addresses = self._ensure_addresses()
        categories = self._ensure_categories()
        board = self._ensure_board(accounts)
        self._fund_board_escrow(asset, accounts)
        detections = self._ensure_detections(categories, addresses, people)
        handles = self._ensure_handles(detections, people)
        bounties = self._ensure_bounties(board, handles, addresses, people, asset, currency, accounts)
        if self.full:
            self._ensure_claim_and_payment(bounties[0], people["tester"], asset, currency, accounts)
        self.stdout.write(self.style.SUCCESS("Detections ingress complete."))

    def _point(self, longitude, latitude):
        point = Point(longitude, latitude)
        point.srid = 4326
        return point

    def _ensure_payment_asset(self):
        reserve, _ = LedgerAccount.objects.get_or_create(
            code="reserve_main",
            defaults={
                "name": "Main Reserve",
                "account_type": AccountType.RESERVE,
                "active": True,
            },
        )
        escrow, _ = LedgerAccount.objects.get_or_create(
            code="detections-bounty-escrow",
            defaults={
                "name": "Detections Bounty Escrow",
                "account_type": AccountType.RESERVE,
                "active": True,
            },
        )
        asset = Asset.objects.filter(unit_name="TPLN").first()
        if not asset:
            asset = create_asset(
                name="Toto PLN",
                unit_name="TPLN",
                total_supply=Decimal("1000000.00"),
                decimals=2,
                reserve_account=reserve,
                reference="create-tpln-detections",
                description="Internal PLN stablecoin for detections bounty testing.",
                metadata={"ingress": "detections"},
            )
            asset.is_currency = True
            asset.backing_document = "Demo PLN reserve for local testing."
            asset.minting_authority = "Toto Platform Operations"
            asset.save(update_fields=["is_currency", "backing_document", "minting_authority"])
            self.stdout.write("  + asset TPLN")

        currency, _ = Currency.objects.update_or_create(
            code="PLN",
            defaults={
                "name": "Polish Zloty",
                "symbol": "PLN",
                "asset": asset,
                "is_active": True,
            },
        )
        return {"reserve": reserve, "escrow": escrow}, asset, currency

    def _ensure_people(self):
        User = get_user_model()
        tester, created = User.objects.get_or_create(
            username="detections_tester",
            defaults={
                "email": "detections@example.com",
                "is_staff": True,
            },
        )
        if created:
            tester.set_unusable_password()
            tester.save(update_fields=["password"])
            self.stdout.write("  + user detections_tester")

        wallet, _ = LedgerAccount.objects.get_or_create(
            code="user-detections_tester",
            defaults={
                "name": "Detections Tester Wallet",
                "account_type": AccountType.USER,
                "active": True,
                "user": tester,
            },
        )
        if wallet.user_id != tester.pk:
            wallet.user = tester
            wallet.save(update_fields=["user"])

        specs = [
            ("detections-tester", "Detections Tester", "detections@example.com", tester),
            ("field-operator-ewa", "Ewa Field Operator", "ewa.operator@example.com", None),
            ("response-lead-piotr", "Piotr Response Lead", "piotr.response@example.com", None),
        ]
        people = {}
        for slug, name, email, user in specs:
            person, created = Person.objects.get_or_create(
                slug=slug,
                defaults={
                    "display_name": name,
                    "email": email,
                    "user": user,
                },
            )
            updates = []
            if user and person.user_id != user.pk:
                person.user = user
                updates.append("user")
            if not person.email:
                person.email = email
                updates.append("email")
            if updates:
                person.save(update_fields=updates)
            if created:
                self.stdout.write(f"  + person {name}")
            people[slug.split("-")[0] if slug == "detections-tester" else slug] = person
        people["tester"] = people.pop("detections")
        return people

    def _ensure_addresses(self):
        specs = [
            ("warsaw-north-gate", "Warsaw North Gate", "Warsaw", 21.0156, 52.2477),
            ("krakow-river-yard", "Krakow River Yard", "Krakow", 19.9352, 50.0540),
            ("gdansk-dock-seven", "Gdansk Dock Seven", "Gdansk", 18.6717, 54.4067),
        ]
        addresses = {}
        for key, street, city, longitude, latitude in specs:
            address, created = Address.objects.update_or_create(
                street=street,
                locality_name=city,
                defaults={
                    "country_name": "PL",
                    "state_or_province_name": "",
                    "building": "Field point",
                    "geometry": self._point(longitude, latitude),
                },
            )
            if created:
                self.stdout.write(f"  + address {street}")
            addresses[key] = address
        return addresses

    def _ensure_categories(self):
        specs = [
            ("Access Control", "Gate, lock, badge, and perimeter detections."),
            ("Infrastructure", "Physical infrastructure hazards and damage."),
            ("Safety", "Safety hazards requiring field response."),
        ]
        categories = {}
        for name, description in specs:
            category, created = DetectionCategory.objects.get_or_create(
                name=name,
                defaults={"description": description, "is_active": True},
            )
            if created:
                self.stdout.write(f"  + category {name}")
            categories[name] = category
        return categories

    def _ensure_board(self, accounts):
        shop, _ = Shop.objects.get_or_create(
            slug="detections-field-ops",
            defaults={
                "name": "Detections Field Ops",
                "description": "Internal shop for regulated field-response bounty work.",
                "currency": "PLN",
                "ledger_account": accounts["escrow"],
                "is_active": True,
            },
        )
        if shop.ledger_account_id != accounts["escrow"].pk:
            shop.ledger_account = accounts["escrow"]
            shop.save(update_fields=["ledger_account"])

        territory = Territory.objects.filter(name="Polish Royal Cities").first()
        custodian, _ = MarketCustodian.objects.get_or_create(
            shop=shop,
            name="Detections Response Desk",
            defaults={
                "description": "Approves field bounty submissions before reward release.",
                "scope": "all",
                "regulated_product_types": [],
                "is_active": True,
            },
        )
        board, created = BountyBoard.objects.get_or_create(
            slug="field-response",
            defaults={
                "name": "Field Response",
                "shop": shop,
                "territory": territory,
                "description": "Detection-linked work packages for local response teams.",
                "custodian": custodian,
                "ledger_account": accounts["escrow"],
                "currency": "PLN",
                "is_active": True,
            },
        )
        if created:
            self.stdout.write("  + bounty board Field Response")
        return board

    def _fund_board_escrow(self, asset, accounts):
        ref = "detections-escrow-funding-001"
        if LedgerTransaction.objects.filter(reference=ref).exists():
            return
        try:
            transfer_asset(
                asset=asset,
                sender_account=accounts["reserve"],
                receiver_account=accounts["escrow"],
                amount=Decimal("25000.00"),
                reference=ref,
                description="Initial funding for detection bounty escrow.",
            )
            self.stdout.write("  + funded detections bounty escrow")
        except ValidationError as exc:
            self.stdout.write(self.style.WARNING(f"  ! could not fund escrow: {exc}"))

    def _ensure_detections(self, categories, addresses, people):
        now = timezone.now()
        specs = [
            {
                "title": "North gate badge reader offline",
                "description": "Access badge reader is intermittently failing and queueing entry traffic.",
                "category": categories["Access Control"],
                "address": addresses["warsaw-north-gate"],
                "severity": "high",
                "detection_type": "incident",
                "start_time": now - timedelta(hours=3),
            },
            {
                "title": "River yard surface crack",
                "description": "Fresh surface crack near vehicle lane. Needs inspection before heavy traffic resumes.",
                "category": categories["Infrastructure"],
                "address": addresses["krakow-river-yard"],
                "severity": "medium",
                "detection_type": "hazard",
                "start_time": now - timedelta(hours=5),
            },
            {
                "title": "Dock seven loose barrier",
                "description": "Temporary barrier has shifted toward pedestrian flow at the dock approach.",
                "category": categories["Safety"],
                "address": addresses["gdansk-dock-seven"],
                "severity": "low",
                "detection_type": "observation",
                "start_time": now - timedelta(hours=8),
            },
        ]
        detections = []
        for spec in specs:
            detection, created = Detection.objects.update_or_create(
                title=spec["title"],
                defaults={
                    **spec,
                    "status": "new",
                    "reported_by": people["field-operator-ewa"],
                    "public": True,
                },
            )
            if created:
                self.stdout.write(f"  + detection {detection.title}")
            detections.append(detection)
        return detections

    def _ensure_handles(self, detections, people):
        handles = []
        for detection in detections:
            handle, created = DetectionHandle.objects.get_or_create(
                detection=detection,
                defaults={
                    "assigned_to": people["response-lead-piotr"],
                    "status": "assigned",
                    "note": "Seeded response handle for local bounty testing.",
                },
            )
            if created:
                self.stdout.write(f"  + handle for {detection.title}")
            handles.append(handle)
        return handles

    def _ensure_bounties(self, board, handles, addresses, people, asset, currency, accounts):
        specs = [
            ("Repair badge reader and verify entry logs", handles[0], addresses["warsaw-north-gate"], Decimal("450.00"), "high-priority field response"),
            ("Inspect and document river yard crack", handles[1], addresses["krakow-river-yard"], Decimal("220.00"), "inspection with photos and recommendation"),
            ("Reset dock barrier and submit safety photo", handles[2], addresses["gdansk-dock-seven"], Decimal("120.00"), "short on-site safety task"),
        ]
        bounties = []
        for title, handle, address, amount, summary in specs:
            bounty, created = Bounty.objects.get_or_create(
                board=board,
                title=title,
                defaults={
                    "category": handle.detection.category,
                    "handle": handle,
                    "created_by": people["response-lead-piotr"],
                    "summary": summary,
                    "description": f"Resolve detection: {handle.detection.title}. Submit evidence and notes for custodian approval.",
                    "bounty_type": "task",
                    "status": "open",
                    "reward_amount": amount,
                    "reward_currency": currency,
                    "reward_asset": asset,
                    "reward_ledger_account": accounts["escrow"],
                    "location": address,
                    "deadline": timezone.now() + timedelta(days=3),
                    "is_public": True,
                },
            )
            if created:
                self.stdout.write(f"  + bounty {title}")
            bounties.append(bounty)
        return bounties

    def _ensure_claim_and_payment(self, bounty, hunter, asset, currency, accounts):
        claim, created = BountyClaim.objects.get_or_create(
            bounty=bounty,
            hunter=hunter,
            defaults={
                "user": hunter.user,
                "status": "approved",
                "proposal": "Seeded full-mode claim for payment testing.",
                "accepted_at": timezone.now() - timedelta(hours=2),
                "completed_at": timezone.now() - timedelta(hours=1),
            },
        )
        if claim.user_id != hunter.user_id:
            claim.user = hunter.user
            claim.save(update_fields=["user", "updated_at"])
        if created:
            self.stdout.write("  + approved bounty claim")

        BountySubmission.objects.get_or_create(
            claim=claim,
            defaults={
                "title": "Seeded completion evidence",
                "body": "The field task was completed and evidence was attached in local testing data.",
                "status": "approved",
                "reviewer_note": "Approved by ingress seed.",
                "reviewed_at": timezone.now(),
            },
        )
        receiver = LedgerAccount.objects.filter(user=hunter.user, active=True).order_by("pk").first()
        payment, created = BountyPayment.objects.get_or_create(
            claim=claim,
            defaults={
                "ledger_account": accounts["escrow"],
                "receiver_ledger_account": receiver,
                "asset": asset,
                "amount": bounty.reward_amount,
                "currency": currency,
                "note": "Seeded unsettled payment. Use the dashboard to transfer the asset.",
                "is_settled": False,
            },
        )
        if created:
            self.stdout.write(f"  + unsettled payment {payment.amount} {asset.unit_name}")
