# file_lambda_helper.py
import io
import json
import numpy as np
from django.core.files.base import ContentFile
from toto.executor import RestrictedPythonExecutor


class FileLambdaHelper:
    Error = RestrictedPythonExecutor.ExecutionError

    @staticmethod
    def _normalize_file(result):
        """
        Strict validation:
        Lambda MUST return:
            { "name": <str>, "content": <bytes|BytesIO> }
        """

        if not isinstance(result, dict):
            raise FileLambdaHelper.Error(
                f"Lambda must return dict with 'name' and 'content', got: {type(result).__name__}"
            )

        if "name" not in result or "content" not in result:
            raise FileLambdaHelper.Error(
                "Lambda must return dict with keys: 'name' and 'content'"
            )

        name = result["name"]
        content = result["content"]

        if not isinstance(name, str):
            raise FileLambdaHelper.Error("Returned 'name' must be a string")

        # Accept bytes or BytesIO
        if isinstance(content, io.BytesIO):
            content = content.getvalue()
        elif not isinstance(content, (bytes, bytearray)):
            raise FileLambdaHelper.Error(
                "Returned 'content' must be bytes or BytesIO"
            )

        return ContentFile(content, name=name)

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
            "io": io,
            "json": json,
        })

        return FileLambdaHelper._normalize_file(result)
