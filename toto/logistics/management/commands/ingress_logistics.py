from django.utils import timezone

from toto.ingress import IngressCommand

from ...models import Package, PackageEvent, PackageStatus, Transport, TransportMode


class Command(IngressCommand):
    help = "Seed the Logistics app with demo transports, packages, and tracking events."

    def process(self):
        self.stdout.write("📦  Seeding Logistics…")

        transports = self._seed_transports()
        self._seed_packages(transports)

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
