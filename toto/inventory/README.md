# toto.inventory

Physical asset registry. Tracks real-world objects from acquisition through lifecycle events. Objects can be tokenized on the ledger via `assets.Tokenization`.

## Purpose

A community catalogs its physical property — vehicles, tools, equipment, real estate — as `RealWorldObject` records. Items move through condition and status stages over time. Tokenizing an object issues an on-ledger `Asset` that represents it, enabling ownership transfer and collateralization through the financial system. During emergencies, inventory items can be requisitioned for deployment use via `EmergencyEquipmentAccess`; during normal operations they are allocated to specific deployments via `DeploymentEquipment`.

## Models

- `ObjectType` — extends `DomainEntity`. Classification for real-world objects (e.g. "Vehicle", "Medical Equipment", "Tool"). Has `category` slug and `unit_of_measure`.

- `RealWorldObject` — extends `DomainEntity`. The core record. Key fields:
  - `object_type` — FK to `ObjectType`
  - `owner` — FK to `socialhub.Community` (nullable; community-owned items)
  - `custodian` — FK to `people.Person` (nullable; person currently responsible)
  - `serial_number`, `model_name`, `manufacturer`
  - `acquisition_date`, `acquisition_cost_base_units`, `cost_asset`
  - `condition` — `new / good / fair / poor / damaged / decommissioned`
  - `status` — `in_service / in_storage / in_transit / maintenance / decommissioned`
  - `storage_location` — FK to `StorageLocation`
  - `notes`

- `InventorySite` — extends `DomainEntity`. A physical facility that holds objects. Fields: `community` FK, `address` FK to `locations.Address`, `site_type` (`warehouse / depot / field_station / vehicle`), `is_active`.

- `StorageLocation` — extends `DomainEntity`. A named position within a site (shelf, bay, room). Fields: `site` FK, `code`, `capacity`.

## Key coupling

- `assets.Tokenization` — links a `RealWorldObject` one-to-one to an `assets.Asset`. Permanent record; delete blocked.
- `response.DeploymentEquipment` — inventory items are allocated to deployments.
- `mobilization.EmergencyEquipmentAccess` — items can be granted hybrid access under emergencies.
- `bazaar.Product` — physical products may reference an inventory item.
- `tribunal.TribunalCase` — cases can concern a specific `RealWorldObject`.

## Dependencies

- `assets` — RealWorldObject can be tokenized as an Asset
- `locations` — InventorySite address and StorageLocation
- `people` — Object ownership and custodian Person FKs
