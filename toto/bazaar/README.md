# toto.bazaar

Community marketplace. Handles shops, product catalogs, cart → order → payment flows, inventory, shipping, and coupons. Payments are settled through the `assets` ledger.

## Purpose

A community opens a `Shop`, vendors list `Product` records, and buyers browse → add to cart → checkout. Payment settles as a ledger transfer or creates an `Obligation` for credit terms. `MarketCustodian` records let a regulated body approve products before they go live and review service deliveries before payment is released. Community transaction fees (set by assembly vote) are applied automatically at checkout. Disputes over orders feed into the `tribunal`.

## Models

- `Shop` — a community storefront. FK to `socialhub.Community`. Has `is_active`, `slug`, M2M to `Person` (managers).
- `Vendor` — a supplier within a shop. FK to `Shop` and `people.Person`.
- `ProductCategory` — hierarchical tree (self-referential parent FK).
- `Product` — a listing. Fields: `shop`, `vendor`, `category`, `name`, `description`, `price_base_units`, `price_asset` (FK to `assets.Asset`), `stock_quantity`, `product_type` (`physical / service / digital`), `is_active`. Has `ProductImage` and `ProductVariant` children.
- `ProductVariant` — size/color/option variant of a product with its own price override.
- `InventoryMovement` — ledger of stock changes (`restock / sale / adjustment / return`).
- `Cart` / `CartItem` — session-scoped pre-order container.
- `Order` — a confirmed purchase. Fields: `shop`, `buyer` (FK to `people.Person`), `status` (`pending / paid / processing / shipped / delivered / cancelled / refunded`), `total_base_units`, `currency_asset`, `payment_method` (`ledger / obligation / cash`).
- `OrderItem` — line items on an order.
- `ServiceDelivery` — delivery record for service-type products. Has a `completed_at` and `delivery_notes`.
- `OrderStatusEvent` — append-only status history for an order.
- `PaymentIntent` — tracks a payment attempt. FK to `Order`. Has `status` and `metadata`.
- `PaymentTransaction` — links an `Order` to an `assets.LedgerTransaction`.
- `ShippingMethod` / `Shipment` — shipping configuration and per-order shipment tracking.
- `Coupon` — discount code with `discount_type` (`flat / percentage`), usage limit, expiry.
- `OrderDiscount` — links a `Coupon` to an `Order`.

## Services (`services.py`)

| Function | What it does |
|---|---|
| `get_or_create_cart(request, shop)` | Returns or creates a cart for the session |
| `add_product_to_cart(request, product, ...)` | Adds/updates a cart item; validates stock |
| `recalculate_cart(cart)` | Recomputes subtotal, fees, and coupon discounts |
| `validate_cart(cart)` | Checks stock, active status, and required fields |
| `create_order_from_cart(cart, checkout_data)` | Converts cart to `Order`; locks inventory |
| `reserve_inventory(order)` | Creates `InventoryMovement(sale)` records |
| `mark_order_paid(order, payment_data)` | Sets `paid`; triggers `PaymentTransaction` |
| `pay_order_with_ledger(order, buyer_account, asset)` | Posts ledger entries for payment |
| `create_obligation_for_order(order, ...)` | Creates `assets.Obligation` for credit-term orders |
| `apply_coupon(cart, code)` | Validates and applies coupon |
| `product_unit_price(product, variant, currency)` | Returns display price in target currency |

## Key coupling

- `assets.LedgerAccount` — buyer and seller accounts for ledger payments.
- `assembly.CommunityTransactionFee` — bazaar reads community fee records and applies them at checkout.
- `inventory.RealWorldObject` — physical products can link to inventory items.
- `tribunal.TribunalCase` — cases can be linked to a disputed order.

## Dependencies

- `assets` — Payments post to LedgerAccount; fees reference LedgerAccount
- `locations` — Shipping addresses; shop location
- `people` — Vendor and buyer are Person records
- `socialhub` — Every shop is community-scoped
