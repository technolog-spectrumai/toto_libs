# toto.logistics

Transport and package tracking. Models a fleet of vehicles/carriers, packages in transit, and timestamped package events. Extends `DomainEntity`.

## Models

- `TransportMode` — text choices: `road / rail / water / air / foot / mixed`.

- `Transport` — a carrier (vehicle, vessel, courier). Fields: `name`, `mode` (FK to `TransportMode`), `community` (FK), `capacity`, `is_active`, `current_location` / `origin` / `destination` (FKs to `locations.Address`), `operator` (FK to `people.Person`).

- `Package` — a shipment. Fields: `reference` (unique), `transport` (FK, nullable), `shipment` (OneToOne FK to `bazaar.Shipment`, nullable), `origin` / `destination` (FKs to `locations.Address`), `weight_kg`, `volume_cm3`, `status` (`pending / picked_up / in_transit / out_for_delivery / delivered / failed / returned`), `expected_at`, `delivered_at`.

- `PackageEvent` — append-only status event. Fields: `package`, `transport` (FK, nullable), `event_type` (`status_change / location_update / note`), `location` (FK to `locations.Address`, nullable), `note`, `occurred_at`.

## Key coupling

- `bazaar.Shipment` — each bazaar shipment can be tracked as a `Package` (OneToOne).
- `locations.Address` — origin, destination, and current location anchors.
