# toto.ingress

Base class for seed/ingress management commands. Not an app with models — provides `IngressCommand`, the common base for all `python manage.py ingress_*` commands.

## What it provides

- `IngressCommand(BaseCommand)` — abstract base. Provides:
  - `DATA_ROOT` — resolved path to the `data/` directory at repo root
  - `read_text(*parts)` — reads a text file from `DATA_ROOT`
  - `read_json(*parts)` — reads and parses a JSON file from `DATA_ROOT`
  - `--full` flag — subcommands check `self.full` to decide whether to run extended seeding
  - `process()` — abstract; subclasses implement this

## Usage

Each app's ingress command subclasses `IngressCommand`:

```python
from toto.ingress.management.commands import IngressCommand

class Command(IngressCommand):
    def process(self):
        # seed data here
        ...
```

Run with: `python manage.py ingress_<app_name> [--full]`

## Dependencies

None — standalone app.
