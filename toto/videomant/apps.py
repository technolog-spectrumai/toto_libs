from django.apps import AppConfig


_CELERY_TASKS = [
    ("videomant.probe",       "toto.videomant.tasks.probe"),
    ("videomant.compress",    "toto.videomant.tasks.compress"),
    ("videomant.resize",      "toto.videomant.tasks.resize"),
    ("videomant.cut",         "toto.videomant.tasks.cut"),
    ("videomant.extract_mp3", "toto.videomant.tasks.extract_mp3"),
    ("videomant.thumbnail",   "toto.videomant.tasks.thumbnail"),
    ("videomant.gif",         "toto.videomant.tasks.gif"),
    ("videomant.concat",      "toto.videomant.tasks.concat"),
]


class VideoMantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.videomant"
    label = "videomant"
    verbose_name = "VideoMant"

    def ready(self):
        from toto.workflows import predefined_tasks
        for task_name, celery_name in _CELERY_TASKS:
            predefined_tasks.register_celery(task_name, celery_name)
