from django.utils import timezone

from toto.ingress import IngressCommand

from ...models import Package, PackageEvent, PackageStatus, Transport, TransportMode


class Command(IngressCommand):
    help = "Seed the Logistics app with demo transports, packages, and tracking events."

    def process(self):
        self.stdout.write("📦  Seeding Logistics…")

        transports = self._seed_transports()
        self._seed_packages(transports)
        self._seed_fleet()

        self.stdout.write(self.style.SUCCESS("✅  Logistics ingress complete."))

    # ------------------------------------------------------------------ #

    def _get_address(self, pk):
        from toto.locations.models import Address
        try:
            return Address.objects.get(pk=pk)
        except Address.DoesNotExist:
            return None

    # ------------------------------------------------------------------ #

    def _seed_transports(self) -> dict:
        # Use Polish / UK addresses from existing fixtures
        warsaw_royal   = self._get_address(16)   # Warsaw — Royal Castle
        krakow_wawel   = self._get_address(18)   # Krakow — Wawel Castle
        gdansk_market  = self._get_address(20)   # Gdansk — Long Market
        london_tower   = self._get_address(15)   # London — Tower of London
        paris_eiffel   = self._get_address(1)    # Paris — Eiffel Tower

        specs = [
            dict(
                name="Orzel Express",
                carrier="Toto Logistics",
                mode=TransportMode.ROAD,
                identifier="TL-ROAD-001",
                origin=warsaw_royal,
                destination=gdansk_market,
                current_location=krakow_wawel,
                is_active=True,
            ),
            dict(
                name="Vistula Rail Freight",
                carrier="PKP Cargo",
                mode=TransportMode.RAIL,
                identifier="PKP-4471",
                origin=warsaw_royal,
                destination=krakow_wawel,
                current_location=warsaw_royal,
                is_active=True,
            ),
            dict(
                name="Channel Courier",
                carrier="EuroParcel",
                mode=TransportMode.COURIER,
                identifier="EP-CC-88",
                origin=london_tower,
                destination=paris_eiffel,
                current_location=london_tower,
                is_active=True,
            ),
            dict(
                name="Baltic Air Cargo",
                carrier="LOT Air Freight",
                mode=TransportMode.AIR,
                identifier="LO-F772",
                origin=gdansk_market,
                destination=london_tower,
                current_location=gdansk_market,
                is_active=False,
            ),
        ]

        transports = {}
        for spec in specs:
            transport, created = Transport.objects.get_or_create(
                name=spec["name"],
                defaults={k: v for k, v in spec.items() if k != "name"},
            )
            transports[spec["name"]] = transport
            if created:
                self.stdout.write(f"  + transport '{transport.name}' ({transport.get_mode_display()})")
            else:
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing transport '{transport.name}'"))
        return transports

    # ------------------------------------------------------------------ #

    def _seed_packages(self, transports: dict):
        warsaw   = self._get_address(16)
        krakow   = self._get_address(18)
        gdansk   = self._get_address(20)
        westerp  = self._get_address(22)   # Gdansk — Westerplatte
        london   = self._get_address(15)
        paris    = self._get_address(1)
        stoneh   = self._get_address(14)   # Amesbury — Stonehenge

        orzel    = transports.get("Orzel Express")
        vistula  = transports.get("Vistula Rail Freight")
        channel  = transports.get("Channel Courier")

        now = timezone.now()

        specs = [
            # --- In-transit road package ---
            dict(
                tracking_number="TL-2026-00001",
                carrier="Toto Logistics",
                status=PackageStatus.IN_TRANSIT,
                transport=orzel,
                origin=warsaw,
                destination=gdansk,
                description="Electronics — laptop and accessories",
                estimated_delivery=now + timezone.timedelta(days=1),
                events=[
                    dict(status=PackageStatus.CREATED,    transport=None,   location=warsaw,  note="Package registered at Warsaw depot.",          occurred_at=now - timezone.timedelta(hours=18)),
                    dict(status=PackageStatus.PICKED_UP,  transport=orzel,  location=warsaw,  note="Picked up by Orzel Express driver.",            occurred_at=now - timezone.timedelta(hours=16)),
                    dict(status=PackageStatus.IN_TRANSIT, transport=orzel,  location=krakow,  note="Scanned at Krakow transit hub.",                occurred_at=now - timezone.timedelta(hours=8)),
                ],
            ),
            # --- Rail package at hub ---
            dict(
                tracking_number="TL-2026-00002",
                carrier="PKP Cargo",
                status=PackageStatus.AT_HUB,
                transport=vistula,
                origin=warsaw,
                destination=krakow,
                description="Books and printed materials",
                estimated_delivery=now + timezone.timedelta(hours=12),
                events=[
                    dict(status=PackageStatus.CREATED,    transport=None,    location=warsaw, note="Dispatched from Warsaw central.",               occurred_at=now - timezone.timedelta(hours=24)),
                    dict(status=PackageStatus.PICKED_UP,  transport=vistula, location=warsaw, note="Loaded onto Vistula Rail Freight.",              occurred_at=now - timezone.timedelta(hours=22)),
                    dict(status=PackageStatus.AT_HUB,     transport=vistula, location=krakow, note="Arrived at Krakow freight terminal.",           occurred_at=now - timezone.timedelta(hours=4)),
                ],
            ),
            # --- Out for delivery ---
            dict(
                tracking_number="TL-2026-00003",
                carrier="Toto Logistics",
                status=PackageStatus.OUT_FOR_DELIVERY,
                transport=orzel,
                origin=krakow,
                destination=gdansk,
                description="Clothing and personal items",
                estimated_delivery=now + timezone.timedelta(hours=3),
                events=[
                    dict(status=PackageStatus.CREATED,          transport=None,  location=krakow,  note="",                                        occurred_at=now - timezone.timedelta(hours=30)),
                    dict(status=PackageStatus.PICKED_UP,        transport=orzel, location=krakow,  note="Collected from sender.",                  occurred_at=now - timezone.timedelta(hours=28)),
                    dict(status=PackageStatus.IN_TRANSIT,       transport=orzel, location=warsaw,  note="In transit via Warsaw.",                   occurred_at=now - timezone.timedelta(hours=14)),
                    dict(status=PackageStatus.AT_HUB,           transport=orzel, location=westerp, note="Arrived at Gdansk sorting facility.",      occurred_at=now - timezone.timedelta(hours=5)),
                    dict(status=PackageStatus.OUT_FOR_DELIVERY, transport=orzel, location=gdansk,  note="Out for delivery — expected before 18:00.", occurred_at=now - timezone.timedelta(hours=1)),
                ],
            ),
            # --- Delivered ---
            dict(
                tracking_number="TL-2026-00004",
                carrier="EuroParcel",
                status=PackageStatus.DELIVERED,
                transport=None,
                origin=london,
                destination=paris,
                description="Art prints",
                estimated_delivery=now - timezone.timedelta(days=1),
                events=[
                    dict(status=PackageStatus.CREATED,    transport=None,    location=london, note="",                                              occurred_at=now - timezone.timedelta(days=4)),
                    dict(status=PackageStatus.PICKED_UP,  transport=channel, location=london, note="Collected from London sender.",                 occurred_at=now - timezone.timedelta(days=3, hours=20)),
                    dict(status=PackageStatus.IN_TRANSIT, transport=channel, location=stoneh, note="In transit — heading to channel crossing.",     occurred_at=now - timezone.timedelta(days=3)),
                    dict(status=PackageStatus.AT_HUB,     transport=channel, location=paris,  note="Cleared customs at Paris CDG depot.",           occurred_at=now - timezone.timedelta(days=2)),
                    dict(status=PackageStatus.DELIVERED,  transport=None,    location=paris,  note="Delivered to recipient.",                       occurred_at=now - timezone.timedelta(days=1)),
                ],
            ),
            # --- Created only (just registered) ---
            dict(
                tracking_number="TL-2026-00005",
                carrier="Toto Logistics",
                status=PackageStatus.CREATED,
                transport=None,
                origin=gdansk,
                destination=warsaw,
                description="Fragile ceramics",
                estimated_delivery=now + timezone.timedelta(days=3),
                events=[
                    dict(status=PackageStatus.CREATED, transport=None, location=gdansk, note="Label printed, awaiting collection.", occurred_at=now - timezone.timedelta(hours=1)),
                ],
            ),
        ]

        for spec in specs:
            tn = spec["tracking_number"]
            if Package.objects.filter(tracking_number=tn).exists():
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing package {tn}"))
                continue

            events_data = spec.pop("events")
            pkg = Package.objects.create(**spec)

            for ev in events_data:
                PackageEvent.objects.create(package=pkg, **ev)

            self.stdout.write(f"  + package {pkg.tracking_number} [{pkg.get_status_display()}] — {len(events_data)} events")

    # ------------------------------------------------------------------ #

    def _seed_fleet(self):
        from toto.inventory.models import ObjectCategory, ObjectType, RealWorldObject

        self.stdout.write("🚛  Seeding fleet objects…")

        # ── Object types ──────────────────────────────────────────────────
        type_specs = [
            dict(name="Heavy Truck",   slug="heavy-truck",   category=ObjectCategory.VEHICLE,   is_mobile=True,  description="Long-haul road freight truck (>3.5 t)."),
            dict(name="Cargo Van",     slug="cargo-van",     category=ObjectCategory.VEHICLE,   is_mobile=True,  description="Light commercial van for urban deliveries."),
            dict(name="Forklift",      slug="forklift",      category=ObjectCategory.EQUIPMENT, is_mobile=True,  description="Warehouse or yard forklift."),
            dict(name="Mobile Crane",  slug="mobile-crane",  category=ObjectCategory.EQUIPMENT, is_mobile=True,  description="Wheeled crane that can reposition between sites."),
            dict(name="Tanker Truck",  slug="tanker-truck",  category=ObjectCategory.VEHICLE,   is_mobile=True,  description="Road tanker for bulk liquid transport."),
        ]
        types = {}
        for spec in type_specs:
            ot, created = ObjectType.objects.get_or_create(
                slug=spec["slug"],
                defaults={k: v for k, v in spec.items() if k != "slug"},
            )
            types[spec["slug"]] = ot
            verb = "+" if created else "⚠ skipped existing"
            self.stdout.write(f"  {verb} object type '{ot.name}'")

        # ── Fleet objects at seeded addresses ────────────────────────────
        warsaw  = self._get_address(16)
        krakow  = self._get_address(18)
        gdansk  = self._get_address(20)
        london  = self._get_address(15)
        paris   = self._get_address(1)

        fleet_specs = [
            dict(name="TL-TRUCK-001",  object_type=types["heavy-truck"],  location=warsaw,  description="Warsaw depot — northbound run to Gdansk.",     quantity=1, unit="unit"),
            dict(name="TL-TRUCK-002",  object_type=types["heavy-truck"],  location=krakow,  description="Krakow hub — awaiting load assignment.",         quantity=1, unit="unit"),
            dict(name="TL-VAN-001",    object_type=types["cargo-van"],    location=gdansk,  description="Gdansk last-mile delivery van.",                  quantity=1, unit="unit"),
            dict(name="TL-VAN-002",    object_type=types["cargo-van"],    location=warsaw,  description="Warsaw city route van.",                          quantity=1, unit="unit"),
            dict(name="TL-FORK-001",   object_type=types["forklift"],     location=gdansk,  description="Gdansk port forklift — container yard.",          quantity=1, unit="unit"),
            dict(name="TL-FORK-002",   object_type=types["forklift"],     location=krakow,  description="Krakow warehouse forklift.",                      quantity=1, unit="unit"),
            dict(name="TL-CRANE-001",  object_type=types["mobile-crane"], location=warsaw,  description="Mobile crane — construction support fleet.",       quantity=1, unit="unit"),
            dict(name="EP-VAN-001",    object_type=types["cargo-van"],    location=london,  description="EuroParcel London van — channel route.",           quantity=1, unit="unit"),
            dict(name="EP-TANKER-001", object_type=types["tanker-truck"], location=paris,   description="Paris fuel tanker — bulk distribution.",           quantity=1, unit="unit"),
        ]

        for spec in fleet_specs:
            obj, created = RealWorldObject.objects.get_or_create(
                name=spec["name"],
                defaults=spec,
            )
            verb = "+" if created else "⚠ skipped existing"
            self.stdout.write(f"  {verb} fleet object '{obj.name}' ({obj.object_type.name})")
