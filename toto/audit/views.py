from django.shortcuts import get_object_or_404
from django.http import FileResponse, Http404
from django.conf import settings
from pathlib import Path
from .models import AuditLog
from django.contrib.auth.decorators import login_required


@login_required
def view_log(request, appname):
    # Get the AuditLog entry
    audit_entry = get_object_or_404(AuditLog, appname=appname)

    # Resolve full path from settings.LOGGING
    handler_key = f"{appname}_file"
    handler = settings.LOGGING['handlers'].get(handler_key)

    if not handler:
        raise Http404("Log handler not configured for this app.")

    log_path = Path(handler['filename'])

    if not log_path.exists():
        raise Http404("Log file not found.")

    # Return the file as a response
    return FileResponse(open(log_path, 'rb'), as_attachment=True, filename=log_path.name)
