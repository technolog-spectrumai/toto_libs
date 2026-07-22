from dataclasses import dataclass
from typing import List, Tuple, Dict, Any

@dataclass
class BatchActionResult:
    success: List[Any]
    failed: List[Tuple[Any, Exception]]

    @property
    def success_count(self) -> int:
        return len(self.success)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    @property
    def success_titles(self) -> List[str]:
        return [str(obj) for obj in self.success]

    @property
    def failed_titles(self) -> List[str]:
        return [str(obj) for obj, _ in self.failed]

    @property
    def errors(self) -> Dict[str, str]:
        return {str(obj): str(err) for obj, err in self.failed}


class BatchAction:
    def __init__(self, queryset):
        self.queryset = queryset

    def run(self, operation) -> BatchActionResult:
        success = []
        failed = []

        for obj in self.queryset:
            try:
                result = operation(obj)
                success.append(result)
            except Exception as e:
                failed.append((obj, e))

        return BatchActionResult(success=success, failed=failed)

    @staticmethod
    def display_messages(result: BatchActionResult, message_user, request, verb: str = "processed"):
        for title, error in result.errors.items():
            message_user(request, f"❌ Failed to {verb} '{title}': {error}", level='error')

        if result.success_count:
            message_user(
                request,
                f"✅ {verb.capitalize()} {result.success_count} item(s): {', '.join(result.success_titles)}",
                level='info'
            )

        if result.failed_count:
            message_user(
                request,
                f"⚠️ {result.failed_count} item(s) failed to {verb}.",
                level='warning'
            )
