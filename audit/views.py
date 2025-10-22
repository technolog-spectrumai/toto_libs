from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse, Http404
from django.conf import settings
from pathlib import Path
from .models import AuditLog
from .page import PageProcessor
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

    # Read and return log content
    with open(log_path, 'r') as f:
        content = f.read()
    context = {
        'appname': appname,
        'log_content': content,
        'log_path': log_path.name,
    }
    processor = PageProcessor()
    context = processor.decorate(context, request)
    return render(request, 'audit/log_view.html', context)
