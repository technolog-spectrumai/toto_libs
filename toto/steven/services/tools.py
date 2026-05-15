from __future__ import annotations

from datetime import datetime, timezone
from langchain_core.tools import tool


@tool
def echo(text: str) -> str:
    """Echo text back to the user. Useful for testing tool-calling."""
    return text


@tool
def calculator(expression: str) -> str:
    """Evaluate a simple arithmetic expression containing numbers and operators."""
    allowed = set('0123456789+-*/(). %')
    if any(char not in allowed for char in expression):
        return 'Only basic arithmetic characters are allowed.'
    try:
        return str(eval(expression, {'__builtins__': {}}, {}))
    except Exception as exc:
        return f'Calculation error: {exc}'


@tool
def current_time() -> str:
    """Return the current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


TOOL_REGISTRY = {
    'echo': echo,
    'calculator': calculator,
    'current_time': current_time,
}


def tools_for_agent(agent_profile):
    enabled_keys = agent_profile.tools.filter(enabled=True).values_list('key', flat=True)
    return [TOOL_REGISTRY[key] for key in enabled_keys if key in TOOL_REGISTRY]
