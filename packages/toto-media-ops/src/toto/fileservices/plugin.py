"""Re-export. The registry lives in ``toto.vault.plugins``.

It was defined here, in toto-media-ops — a wheel only placidia pins — which meant
the vault could not name the registry that describes what may be done to its own
files. It moved to the app that owns them; this alias keeps every existing
import and every `plugins/file_service_plugins.py` working unchanged.
"""

from toto.vault.plugins import FileServicePlugin  # noqa: F401
