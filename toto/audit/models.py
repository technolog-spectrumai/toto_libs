from django.db import models


class AuditLog(models.Model):
    appname = models.CharField(max_length=100, unique=True)
    filepath = models.CharField(max_length=1024)
    created_at = models.DateTimeField(auto_now=True)

    def str(self):
        return f"{self.appname} → {self.filepath}"
