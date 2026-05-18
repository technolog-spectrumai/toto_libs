from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from toto.ingress import IngressCommand

from ...models import (
    Cart, CartItem, Coupon, Order, OrderItem, OrderStatusEvent,
    Product, ProductCategory, ProductVariant, Shop, Vendor,
)

User = get_user_model()


class Command(IngressCommand):
    help = "Seed the Bazaar with demo shops, products, vendors, carts, and orders."

    def process(self):
        self.stdout.write("🛒  Seeding Bazaar…")

        shops = self._seed_shops()
        categories = self._seed_categories(shops)
        vendors = self._seed_vendors(shops)
        products = self._seed_products(shops, categories, vendors)
        self._seed_variants(products)
        self._seed_coupons(shops)

        if self.full:
            self._seed_orders(shops, vendors, products)

        self.stdout.write(self.style.SUCCESS("✅  Bazaar ingress complete."))

    # ------------------------------------------------------------------ #

    def _seed_shops(self) -> dict:
        specs = [
            dict(
                name="GreenLeaf Market",
                slug="greenleaf-market",
                description="Fresh organic produce, artisan goods, and sustainable living products.",
                currency="PLN",
                is_active=True,
            ),
            dict(
                name="Digital Horizons",
                slug="digital-horizons",
                description="Premium digital products — courses, e-books, software, and media.",
                currency="USD",
                is_active=True,
            ),
            dict(
                name="Craft Corner",
                slug="craft-corner",
                description="Handmade and artisan crafts from independent creators.",
                currency="EUR",
                is_active=False,  # inactive — tests filtering
            ),
        ]
        shops = {}
        for spec in specs:
            slug = spec["slug"]
            shop, created = Shop.objects.get_or_create(
                slug=slug,
                defaults={k: v for k, v in spec.items() if k != "slug"},
            )
            shops[slug] = shop
            if created:
                self.stdout.write(f"  + shop '{shop.name}'")
            else:
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing shop '{shop.name}'"))
        return shops

    # ------------------------------------------------------------------ #

    def _seed_categories(self, shops: dict) -> dict:
        gl = shops.get("greenleaf-market")
        dh = shops.get("digital-horizons")
        cc = shops.get("craft-corner")

        specs = []
        if gl:
            specs += [
                dict(shop=gl, name="Food & Drinks",   slug="food-drinks",   position=1, is_active=True),
                dict(shop=gl, name="Health & Beauty", slug="health-beauty",  position=2, is_active=True),
                dict(shop=gl, name="Home & Garden",   slug="home-garden",   position=3, is_active=True),
            ]
        if dh:
            specs += [
                dict(shop=dh, name="Courses",    slug="courses",    position=1, is_active=True),
                dict(shop=dh, name="E-Books",    slug="ebooks",     position=2, is_active=True),
                dict(shop=dh, name="Music",      slug="music",      position=3, is_active=True),
                dict(shop=dh, name="Software",   slug="software",   position=4, is_active=True),
            ]
        if cc:
            specs += [
                dict(shop=cc, name="Jewellery", slug="jewellery", position=1, is_active=True),
                dict(shop=cc, name="Textiles",  slug="textiles",  position=2, is_active=True),
            ]

        categories = {}
        for spec in specs:
            cat, created = ProductCategory.objects.get_or_create(
                shop=spec["shop"],
                slug=spec["slug"],
                defaults={k: v for k, v in spec.items() if k not in ("shop", "slug")},
            )
            categories[spec["slug"]] = cat
            if created:
                self.stdout.write(f"  + category '{cat.name}' in '{cat.shop.name}'")
        return categories

    # ------------------------------------------------------------------ #

    def _seed_vendors(self, shops: dict) -> dict:
        gl = shops.get("greenleaf-market")
        dh = shops.get("digital-horizons")

        specs = []
        if gl:
            specs += [
                dict(shop=gl, display_name="Farm Direct Co",  slug="farm-direct",   status="active",          bio="Family-run farm supplying seasonal organic produce since 2005."),
                dict(shop=gl, display_name="Urban Organics",  slug="urban-organics", status="active",          bio="City-based cooperative growing herbs and microgreens hydroponically."),
                dict(shop=gl, display_name="Bee Collective",  slug="bee-collective", status="pending_review",  bio="Small apiary producing raw honey and beeswax products."),
            ]
        if dh:
            specs += [
                dict(shop=dh, display_name="CodeCraft Studio", slug="codecraft",  status="active",  bio="Software engineers turned educators. Practical courses on Python, Go, and DevOps."),
                dict(shop=dh, display_name="Nova Media",       slug="nova-media", status="active",  bio="Producers of high-quality stock music and design assets."),
            ]

        vendors = {}
        for spec in specs:
            vendor, created = Vendor.objects.get_or_create(
                shop=spec["shop"],
                slug=spec["slug"],
                defaults={k: v for k, v in spec.items() if k not in ("shop", "slug")},
            )
            vendors[spec["slug"]] = vendor
            if created:
                self.stdout.write(f"  + vendor '{vendor.display_name}'")
        return vendors

    # ------------------------------------------------------------------ #

    def _seed_products(self, shops: dict, categories: dict, vendors: dict) -> dict:
        gl = shops.get("greenleaf-market")
        dh = shops.get("digital-horizons")

        specs = []
        if gl:
            fd_vendor  = vendors.get("farm-direct")
            uo_vendor  = vendors.get("urban-organics")
            fd_cat = categories.get("food-drinks")
            hb_cat = categories.get("health-beauty")
            hg_cat = categories.get("home-garden")
            specs += [
                dict(
                    shop=gl, vendor=fd_vendor, category=fd_cat,
                    name="Raw Forest Honey 500g", slug="raw-honey-500g",
                    summary="Unfiltered, cold-extracted honey from free-range hives.",
                    description="Harvested from wildflower forests, our raw honey retains all enzymes, pollen, and antioxidants. No heating. No additives.",
                    product_type="physical", status="published", is_public=True, is_featured=True,
                    price=Decimal("28.90"), currency="PLN",
                    stock_tracking_enabled=True, stock_quantity=240,
                ),
                dict(
                    shop=gl, vendor=uo_vendor, category=fd_cat,
                    name="Herb Microgreens Box", slug="herb-microgreens-box",
                    summary="Weekly box of fresh-cut microgreens — basil, radish, sunflower.",
                    description="Grown hydroponically in our city greenhouse. Harvested to order. Ships within 24 h.",
                    product_type="subscription", status="published", is_public=True, is_featured=False,
                    price=Decimal("45.00"), currency="PLN",
                    stock_tracking_enabled=False,
                ),
                dict(
                    shop=gl, vendor=fd_vendor, category=hb_cat,
                    name="Cold-Pressed Rosehip Oil 30ml", slug="rosehip-oil-30ml",
                    summary="100% pure rosehip seed oil, first cold press.",
                    description="Rich in vitamin A and essential fatty acids. Suitable for face, hair, and body. Certified organic.",
                    product_type="physical", status="published", is_public=True, is_featured=False,
                    price=Decimal("59.00"), currency="PLN",
                    stock_tracking_enabled=True, stock_quantity=85,
                ),
                dict(
                    shop=gl, vendor=uo_vendor, category=hg_cat,
                    name="Bamboo Herb Planter Set", slug="bamboo-herb-planter",
                    summary="Three-pot bamboo planter with organic soil and seed packets.",
                    description="Grow basil, mint, and parsley on your windowsill. Fully compostable packaging. Great gift.",
                    product_type="physical", status="published", is_public=True, is_featured=True,
                    price=Decimal("79.00"), currency="PLN",
                    stock_tracking_enabled=True, stock_quantity=50,
                ),
                dict(
                    shop=gl, vendor=fd_vendor, category=fd_cat,
                    name="Heritage Grain Flour 1kg", slug="heritage-grain-flour",
                    summary="Stone-milled spelt and einkorn blend for artisan baking.",
                    description="Single-origin, stone-milled on a 19th-century mill. Excellent for sourdough and pasta.",
                    product_type="physical", status="draft", is_public=False, is_featured=False,
                    price=Decimal("14.50"), currency="PLN",
                    stock_tracking_enabled=True, stock_quantity=0,
                ),
            ]

        if dh:
            cc_vendor = vendors.get("codecraft")
            nm_vendor = vendors.get("nova-media")
            co_cat = categories.get("courses")
            eb_cat = categories.get("ebooks")
            mu_cat = categories.get("music")
            sw_cat = categories.get("software")
            specs += [
                dict(
                    shop=dh, vendor=cc_vendor, category=co_cat,
                    name="Python Mastery: From Zero to Production", slug="python-mastery",
                    summary="40-hour hands-on Python course covering web, data, and automation.",
                    description="Project-based curriculum. Build a REST API, a data pipeline, and a CLI tool. Lifetime access.",
                    product_type="digital", status="published", is_public=True, is_featured=True,
                    price=Decimal("149.00"), currency="USD",
                    stock_tracking_enabled=False,
                ),
                dict(
                    shop=dh, vendor=cc_vendor, category=eb_cat,
                    name="The Go Concurrency Handbook", slug="go-concurrency-handbook",
                    summary="Deep dive into goroutines, channels, and concurrency patterns.",
                    description="300-page e-book in PDF and EPUB. Code examples on GitHub. Updated for Go 1.22.",
                    product_type="digital", status="published", is_public=True, is_featured=False,
                    price=Decimal("29.00"), currency="USD",
                    stock_tracking_enabled=False,
                ),
                dict(
                    shop=dh, vendor=nm_vendor, category=mu_cat,
                    name="Ambient Focus Pack Vol. 1", slug="ambient-focus-pack-1",
                    summary="12 royalty-free ambient tracks for work, study, and meditation.",
                    description="WAV + MP3. Total runtime 84 minutes. Commercial licence included. Perfect for YouTube and podcasts.",
                    product_type="digital", status="published", is_public=True, is_featured=False,
                    price=Decimal("19.00"), currency="USD",
                    stock_tracking_enabled=False,
                ),
                dict(
                    shop=dh, vendor=nm_vendor, category=sw_cat,
                    name="IconForge Pro — 2500 UI Icon Set", slug="iconforge-pro",
                    summary="Vector icon set for web and mobile, 6 styles, SVG + Figma.",
                    description="2 500 icons in stroke, filled, duotone, flat, hand-drawn, and neon variants. Perpetual licence.",
                    product_type="digital", status="published", is_public=True, is_featured=True,
                    price=Decimal("49.00"), currency="USD",
                    stock_tracking_enabled=False,
                ),
                dict(
                    shop=dh, vendor=cc_vendor, category=co_cat,
                    name="DevOps Bootcamp — Docker & Kubernetes", slug="devops-bootcamp",
                    summary="Containerise, orchestrate, and deploy production-grade services.",
                    description="30-hour video course with lab environments. Covers Docker Compose, Helm, ArgoCD, and more.",
                    product_type="digital", status="hidden", is_public=True, is_featured=False,
                    price=Decimal("119.00"), currency="USD",
                    stock_tracking_enabled=False,
                ),
            ]

        products = {}
        for spec in specs:
            slug = spec["slug"]
            shop = spec["shop"]
            prod, created = Product.objects.get_or_create(
                shop=shop,
                slug=slug,
                defaults={k: v for k, v in spec.items() if k not in ("shop", "slug")},
            )
            products[slug] = prod
            if created:
                if spec["status"] == "published":
                    prod.published_at = timezone.now()
                    prod.save(update_fields=["published_at"])
                self.stdout.write(f"  + product '{prod.name}'")
        return products

    # ------------------------------------------------------------------ #

    def _seed_variants(self, products: dict):
        honey = products.get("raw-honey-500g")
        if honey:
            for name, sku, price, qty in [
                ("500 g",  "HONEY-500",  None,             240),
                ("1 kg",   "HONEY-1KG",  Decimal("52.00"), 80),
                ("2 kg",   "HONEY-2KG",  Decimal("98.00"), 30),
            ]:
                _, created = ProductVariant.objects.get_or_create(
                    product=honey,
                    sku=sku,
                    defaults={"name": name, "price": price, "stock_quantity": qty, "is_active": True},
                )
                if created:
                    self.stdout.write(f"  + variant '{name}' for '{honey.name}'")

        planter = products.get("bamboo-herb-planter")
        if planter:
            for name, sku in [
                ("Small (2-pot)", "BHP-SM"),
                ("Standard (3-pot)", "BHP-STD"),
                ("Large (5-pot)", "BHP-LG"),
            ]:
                _, created = ProductVariant.objects.get_or_create(
                    product=planter,
                    sku=sku,
                    defaults={"name": name, "stock_quantity": 20, "is_active": True},
                )
                if created:
                    self.stdout.write(f"  + variant '{name}' for '{planter.name}'")

    # ------------------------------------------------------------------ #

    def _seed_coupons(self, shops: dict):
        gl = shops.get("greenleaf-market")
        dh = shops.get("digital-horizons")

        specs = []
        if gl:
            specs += [
                dict(shop=gl, code="WELCOME10", discount_type="percentage",    value=Decimal("10.00"), is_active=True),
                dict(shop=gl, code="FLAT5",     discount_type="fixed_amount",  value=Decimal("5.00"),  is_active=True),
            ]
        if dh:
            specs += [
                dict(shop=dh, code="LAUNCH20",  discount_type="percentage",    value=Decimal("20.00"), is_active=True),
                dict(shop=dh, code="FREESHIP",  discount_type="free_shipping", value=Decimal("0.00"),  is_active=False),
            ]

        for spec in specs:
            _, created = Coupon.objects.get_or_create(
                shop=spec["shop"],
                code=spec["code"],
                defaults={k: v for k, v in spec.items() if k not in ("shop", "code")},
            )
            if created:
                self.stdout.write(f"  + coupon '{spec['code']}' in '{spec['shop'].name}'")

    # ------------------------------------------------------------------ #
    # Orders (only with --full)                                            #
    # ------------------------------------------------------------------ #

    def _seed_orders(self, shops: dict, vendors: dict, products: dict):
        gl = shops.get("greenleaf-market")
        dh = shops.get("digital-horizons")

        honey   = products.get("raw-honey-500g")
        planter = products.get("bamboo-herb-planter")
        rosehip = products.get("rosehip-oil-30ml")
        python_ = products.get("python-mastery")
        icons   = products.get("iconforge-pro")
        ebook   = products.get("go-concurrency-handbook")

        orders_spec = []

        if gl and honey and planter:
            orders_spec.append(dict(
                order_number="BZR-GL-001",
                shop=gl,
                email="alice@example.com",
                status="completed",
                payment_status="paid",
                fulfillment_status="delivered",
                currency="PLN",
                subtotal_amount=Decimal("107.90"),
                discount_amount=Decimal("10.79"),
                shipping_amount=Decimal("9.90"),
                tax_amount=Decimal("0.00"),
                total_amount=Decimal("107.01"),
                paid_at=timezone.now(),
                items=[
                    dict(product=honey,   product_name_snapshot=honey.name,   quantity=2, unit_price=Decimal("28.90"), total_price=Decimal("57.80")),
                    dict(product=planter, product_name_snapshot=planter.name, quantity=1, unit_price=Decimal("79.00"), total_price=Decimal("79.00")),
                ],
                events=[
                    ("placed",     "confirmed",   "Order placed online."),
                    ("confirmed",  "processing",  "Payment received."),
                    ("processing", "completed",   "Shipment delivered."),
                ],
            ))

        if gl and rosehip:
            orders_spec.append(dict(
                order_number="BZR-GL-002",
                shop=gl,
                email="bob@example.com",
                status="placed",
                payment_status="unpaid",
                fulfillment_status="unfulfilled",
                currency="PLN",
                subtotal_amount=Decimal("59.00"),
                discount_amount=Decimal("0.00"),
                shipping_amount=Decimal("9.90"),
                tax_amount=Decimal("0.00"),
                total_amount=Decimal("68.90"),
                paid_at=None,
                items=[
                    dict(product=rosehip, product_name_snapshot=rosehip.name, quantity=1, unit_price=Decimal("59.00"), total_price=Decimal("59.00")),
                ],
                events=[],
            ))

        if gl and honey:
            orders_spec.append(dict(
                order_number="BZR-GL-003",
                shop=gl,
                email="carol@example.com",
                status="cancelled",
                payment_status="refunded",
                fulfillment_status="not_required",
                currency="PLN",
                subtotal_amount=Decimal("57.80"),
                discount_amount=Decimal("0.00"),
                shipping_amount=Decimal("0.00"),
                tax_amount=Decimal("0.00"),
                total_amount=Decimal("57.80"),
                paid_at=None,
                items=[
                    dict(product=honey, product_name_snapshot=honey.name, quantity=2, unit_price=Decimal("28.90"), total_price=Decimal("57.80")),
                ],
                events=[
                    ("placed", "cancelled", "Customer requested cancellation."),
                ],
            ))

        if dh and python_ and icons:
            orders_spec.append(dict(
                order_number="BZR-DH-001",
                shop=dh,
                email="dev@example.com",
                status="completed",
                payment_status="paid",
                fulfillment_status="fulfilled",
                currency="USD",
                subtotal_amount=Decimal("198.00"),
                discount_amount=Decimal("39.60"),
                shipping_amount=Decimal("0.00"),
                tax_amount=Decimal("0.00"),
                total_amount=Decimal("158.40"),
                paid_at=timezone.now(),
                items=[
                    dict(product=python_, product_name_snapshot=python_.name, quantity=1, unit_price=Decimal("149.00"), total_price=Decimal("149.00")),
                    dict(product=icons,   product_name_snapshot=icons.name,   quantity=1, unit_price=Decimal("49.00"),  total_price=Decimal("49.00")),
                ],
                events=[
                    ("placed", "confirmed",  "Auto-confirmed after payment."),
                    ("confirmed", "completed", "Digital delivery complete."),
                ],
            ))

        if dh and ebook:
            orders_spec.append(dict(
                order_number="BZR-DH-002",
                shop=dh,
                email="reader@example.com",
                status="placed",
                payment_status="pending",
                fulfillment_status="unfulfilled",
                currency="USD",
                subtotal_amount=Decimal("29.00"),
                discount_amount=Decimal("5.80"),
                shipping_amount=Decimal("0.00"),
                tax_amount=Decimal("0.00"),
                total_amount=Decimal("23.20"),
                paid_at=None,
                items=[
                    dict(product=ebook, product_name_snapshot=ebook.name, quantity=1, unit_price=Decimal("29.00"), total_price=Decimal("29.00")),
                ],
                events=[],
            ))

        for spec in orders_spec:
            if Order.objects.filter(order_number=spec["order_number"]).exists():
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing order {spec['order_number']}"))
                continue
            items_data  = spec.pop("items")
            events_data = spec.pop("events")
            order = Order.objects.create(**spec)
            for item in items_data:
                OrderItem.objects.create(
                    order=order,
                    vendor=item["product"].vendor,
                    sku_snapshot=item.get("sku_snapshot", ""),
                    **{k: v for k, v in item.items() if k != "sku_snapshot"},
                )
            for prev, new, note in events_data:
                OrderStatusEvent.objects.create(order=order, previous_status=prev, new_status=new, note=note)
            self.stdout.write(f"  + order {order.order_number}  [{order.status}/{order.payment_status}]")
