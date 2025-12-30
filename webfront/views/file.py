import io

from django.views import View
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, Http404
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from vault.file_helper import FileHelper
from webfront.models import FileWorkflow
from oya.page import PageProcessor
from django.core.files.base import ContentFile


@method_decorator(login_required, name="dispatch")
class FileWorkflowView(View):
    template_name = "webfront/gateway.html"

    class NormalizationError(Exception):
        pass

    @staticmethod
    def _normalize_file(result):
        """
        Strict validation:
        Lambda MUST return:
            { "name": <str>, "content": <bytes|BytesIO|None> }

        If content is None → return None (caller decides not to save).
        """
        if result is None:
            return None
        if not isinstance(result, dict):
            raise FileWorkflowView.NormalizationError(
                f"Lambda must return dict with 'name' and 'content', got: {type(result).__name__}"
            )

        if "name" not in result or "content" not in result:
            raise FileWorkflowView.NormalizationError(
                "Lambda must return dict with keys: 'name' and 'content'"
            )
        name = result["name"]
        content = result["content"]

        if not isinstance(name, str):
            raise FileWorkflowView.NormalizationError("Returned 'name' must be a string")

        # BytesIO → extract bytes
        if isinstance(content, io.BytesIO):
            content = content.getvalue()

        # Must be bytes now
        if not isinstance(content, (bytes, bytearray)):
            raise FileWorkflowView.NormalizationError(
                "Returned 'content' must be bytes, BytesIO, or None"
            )

        return ContentFile(content, name=name)

    def get(self, request, slug):
        workflow = get_object_or_404(FileWorkflow, slug=slug)

        if not workflow.is_active:
            raise Http404("Workflow is not active")

        processor = PageProcessor()
        context = processor.decorate({"gateway": workflow}, request)
        return render(request, self.template_name, context)

    def post(self, request, slug):
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
        file_content = self._normalize_file(result)
        vault_file = helper.save_file(file_content)

        if not vault_file:
            return JsonResponse({"error": "File could not be saved"}, status=500)

        return JsonResponse({
            "result": {
                "file_name": vault_file.title,
                "file_key": vault_file.key,
                "file_size_bytes": vault_file.file.size,
            }
        })
