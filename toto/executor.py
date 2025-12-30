import operator
from datetime import timedelta, datetime, date
from RestrictedPython import (
    compile_restricted,
    safe_builtins,
    utility_builtins,
    limited_builtins,
)
from RestrictedPython.Guards import (
    guarded_unpack_sequence,
    full_write_guard,
    guarded_iter_unpack_sequence
)


class RestrictedPythonExecutor:
    """
    Standalone helper for executing restricted Python code.
    Requires the code to define a main(context) function.
    """

    class ExecutionError(Exception):
        pass

    def __init__(self, code: str, name: str = "snippet", dependencies: dict = None):
        self.code = code
        self.name = name
        self.dependencies = dependencies # extra globals

    # ------------------------------------------------------------------
    # Allowed globals
    # ------------------------------------------------------------------
    @staticmethod
    def default_allowed_globals() -> dict:
        allowed_builtins = {}
        allowed_builtins.update(safe_builtins)
        allowed_builtins.update(utility_builtins)
        allowed_builtins.update(limited_builtins)

        return {
            "__builtins__": allowed_builtins,
            "_getitem_": operator.getitem,
            "_setitem_": operator.setitem,
            "_delitem_": operator.delitem,
            "_unpack_sequence_": guarded_unpack_sequence,
            "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
            "_getiter_": iter,
            "_write_": full_write_guard,
            "timedelta": timedelta,
            "datetime": datetime,
            "date": date,
            "sum": sum,
            "len": len,
            "max": max,
            "min": min,
        }

    def build_globals(self):
        allowed_globals = self.default_allowed_globals()
        if self.dependencies:
            allowed_globals.update(self.dependencies)
        return allowed_globals

    def execute(self, context):
        """
        Execute restricted Python code.
        The code must define a main(context) function.
        """
        try:
            byte_code = compile_restricted(
                self.code,
                filename=f"<restricted:{self.name}>",
                mode="exec"
            )

            local_vars = {}
            exec(byte_code, self.build_globals(), local_vars)

            if "main" not in local_vars or not callable(local_vars["main"]):
                return {"error": "No main(context) function defined"}

            return local_vars["main"](context)

        except Exception as e:
            raise self.ExecutionError(f"Execution failed: {e}" ) from e
