from django.shortcuts import get_object_or_404
from django.http import JsonResponse, Http404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST

from vault.file_helper import FileHelper
from webfront.models import FileWorkflow


@login_required
@require_POST
def workflow_upload(request, slug):
    """
    Upload a file directly to a FileWorkflow.
    """

    workflow = get_object_or_404(FileWorkflow, slug=slug)

    if not workflow.is_active:
        raise Http404("Workflow is not active")

    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    if not workflow.lambda_node:
        return JsonResponse({"error": "Workflow has no lambda assigned"}, status=500)

    # Execute lambda
    try:
        result = workflow.lambda_node.execute({"file": uploaded_file})
    except Exception as e:
        return JsonResponse({"error": f"Lambda execution failed: {str(e)}"}, status=500)

    if result is None:
        return JsonResponse({"result": "nothing saved"})

    # Save output file
    helper = FileHelper(bucket_name=workflow.bucket.name)
    vault_file = helper.save_file(result)

    if not vault_file:
        return JsonResponse({"error": "File could not be saved"}, status=500)

    return JsonResponse({
        "result": {
            "file_name": vault_file.title,
            "file_key": vault_file.key,
            "file_size_bytes": vault_file.file.size,
        }
    })
