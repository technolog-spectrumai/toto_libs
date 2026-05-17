# Toto Game

Studio-only MMO RTS economy foundation for Toto.

## Included

- Empire, planet, province, deposit, inventory, building, recipe, construction, transport, and tick models
- Climate-driven biomes and computed planet classification
- Django admin integration
- Player command center, planet overview, province management, and building control views
- Sigma.js functional planet map with selectable province color modes: biome, habitability, infrastructure, population, resources, and production
- YAML-driven engine parameters and seed definitions in `config/economy.yaml`
- `seed_economy` and `ingress_game` management commands
- Celery-compatible global and per-planet tick tasks

## Studio Integration

`toto.game` is loaded only when `BUILD_STUDIO=1`.

The portal routes it at:

```text
/game/
```

Run migrations and ingress in studio mode:

```bash
BUILD_STUDIO=1 python portal/manage.py migrate game
BUILD_STUDIO=1 python portal/manage.py seed_economy
BUILD_STUDIO=1 python portal/manage.py seed_economy --demo-user your_username
```

Full ingress creates demo data for the first user when one exists:

```bash
BUILD_STUDIO=1 FULL_INGRESS=1 python portal/manage.py ingress_game --full
```

## Celery

When studio mode is enabled, `portal.settings` schedules:

```text
toto.game.tasks.run_global_game_tick
```

The default cadence is 300 seconds and can be overridden with `GAME_TICK_SECONDS`.

## Next Economy Work

- Procedural climate/province generation from the stored planet config
- Real logistics flow over transport links
- Energy grid constraints
- Workforce constraints
- Market and trade
- Construction input costs
- Extraction buildings controlling deposits
- Production bottleneck reports and recommendations
