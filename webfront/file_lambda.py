# file_lambda_helper.py
import io
import numpy as np
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile, TemporaryUploadedFile
from toto.executor import RestrictedPythonExecutor


class FileLambdaHelper:
    Error = RestrictedPythonExecutor.ExecutionError

    @staticmethod
    def _normalize_file(result, original_file):
        """
        Strict validation:
        Only accept real file-like objects.
        If BytesIO is returned, wrap it into ContentFile with a proper name.
        """

        # Already a valid Django uploaded file
        if isinstance(result, (InMemoryUploadedFile, TemporaryUploadedFile, ContentFile)):
            return result

        # BytesIO → wrap into ContentFile
        if isinstance(result, io.BytesIO):
            data = result.getvalue()
            return ContentFile(data, name=original_file.name)

        # Anything else → throw
        raise FileLambdaHelper.Error(
            f"Lambda must return a file-like object (BytesIO or Django file), got: {type(result).__name__}"
        )

    @staticmethod
    def apply_user_lambda(code: str, file, name: str = "file_lambda"):
        if not code:
            return file

        context = {"file": file}

        executor = RestrictedPythonExecutor(
            code=code,
            context=context,
            name=name
        )

        result = executor.execute(extra_globals={
            "np": np,
            "io": io,  # allow BytesIO creation
        })

        # Normalize + validate
        return FileLambdaHelper._normalize_file(result, file)
