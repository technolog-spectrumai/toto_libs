import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .backup_engine import BackupEngine


class BackupService(BackupEngine):
    """Creates a signed backup ZIP from local shared/core app data."""

    def create_backup(self, output_path=None):
        self._validate_apps_for_create()
        if output_path is None:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            output_path = Path(tempfile.gettempdir()) / f"backup-{ts}.zip"
        else:
            output_path = Path(output_path)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            apps_dir = tmp / "apps"
            apps_dir.mkdir(parents=True, exist_ok=True)

            manifest = self.build_manifest()

            for model in self.iter_models():
                app_dir = apps_dir / model._meta.app_label
                app_dir.mkdir(parents=True, exist_ok=True)

                file_path = app_dir / f"{model._meta.model_name}.json"
                rel_path = str(file_path.relative_to(tmp))

                payload = self.serialize_model(model)
                self.write_json(file_path, payload)

                manifest["models"].append({
                    "app": model._meta.app_label,
                    "model": model._meta.model_name,
                    "label": model._meta.label,
                    "file": rel_path,
                    "count": len(payload["objects"]),
                })
                manifest["hashes"][rel_path] = self.sha256_file(file_path)

            manifest_path = tmp / "manifest.json"
            self.write_json(manifest_path, manifest)
            if self.sign:
                self.write_signature(tmp, manifest_path.read_bytes())
            self.write_zip(tmp, output_path)

        return output_path
