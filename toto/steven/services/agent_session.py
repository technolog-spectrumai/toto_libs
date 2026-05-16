from __future__ import annotations

from abc import ABC, abstractmethod
from importlib.util import find_spec

from django.utils import timezone


def extract_text(agent_response) -> str:
    """Normalize LangChain agent output into displayable text."""
    messages = agent_response.get("messages", []) if isinstance(agent_response, dict) else []

    if messages:
        last = messages[-1]

        if isinstance(last, dict):
            content = last.get("content", "")
        else:
            content = getattr(last, "content", str(last))

        if isinstance(content, list):
            return "\n".join(str(item) for item in content)

        return str(content)

    return str(agent_response)




class AgentSession(ABC):
    """Base interface for running an agent session."""

    def __init__(self, profile):
        self.profile = profile

    def run(self, agent_run):
        """Run and persist an AgentRun using this session implementation."""
        agent_run.status = "running"
        agent_run.started_at = timezone.now()
        agent_run.save(update_fields=["status", "started_at"])

        try:
            agent_run.result = self.invoke(agent_run.user_prompt)
            agent_run.status = "succeeded"
            agent_run.error = ""
        except Exception as exc:
            agent_run.status = "failed"
            agent_run.error = str(exc)
        finally:
            agent_run.finished_at = timezone.now()
            agent_run.save(
                update_fields=[
                    "result",
                    "status",
                    "error",
                    "finished_at",
                ]
            )

        return agent_run

    @abstractmethod
    def invoke(self, user_prompt: str) -> str:
        """Run the agent and return displayable text."""
        raise NotImplementedError


class RealAgentSession(AgentSession):
    """Real LangChain-backed agent session."""

    def _connector_environment(self):
        """Stub — override in subclass or inject vault session to provide env credentials."""
        import contextlib
        return contextlib.nullcontext()

    def invoke(self, user_prompt: str) -> str:
        from langchain.agents import create_agent
        from langchain.chat_models import init_chat_model

        from .tools import tools_for_agent

        if not self.profile.is_active:
            raise RuntimeError(f'Agent "{self.profile.name}" is inactive.')

        if not self.profile.connector:
            raise RuntimeError(f'Agent "{self.profile.name}" has no connector.')

        with self._connector_environment():
            model = init_chat_model(
                self.profile.model_name,
                temperature=self.profile.temperature,
            )

            agent = create_agent(
                model=model,
                tools=tools_for_agent(self.profile),
                system_prompt=self.profile.system_prompt or "",
            )

            response = agent.invoke(
                {
                    "messages": [
                        ("user", user_prompt),
                    ],
                }
            )

        return extract_text(response)


class StubAgentSession(AgentSession):
    """Stub session used when the real agent cannot run."""

    def __init__(self, profile, reason: str | None = None):
        super().__init__(profile)
        self.reason = reason

    def invoke(self, user_prompt: str) -> str:
        from django.conf import settings

        if settings.DEBUG:
            connector = self.profile.connector
            if connector is None:
                connector_info = "None — no connector attached"
            elif not connector.is_active:
                connector_info = f"{connector.name} (inactive)"
            else:
                connector_info = connector.name

            return (
                "[DEBUG] Steven is in stub mode — no real AI call was made.\n"
                "\n"
                f"Agent:     {self.profile.name}\n"
                f"Model:     {self.profile.model_name}\n"
                f"Connector: {connector_info}\n"
                f"Reason:    {self.reason or 'unknown'}\n"
                "\n"
                "Your prompt:\n"
                f"{user_prompt}\n"
                "\n"
                "To enable real AI responses:\n"
                "  Admin → Gervazy → add EncryptedSecret with purpose='openai-api-key'\n"
                "  Admin → Steven → Connectors → attach it to the OpenAI Chat connector\n"
                "  Then re-run: manage.py ingress_steven --full"
            )

        return (
            f"Hi! I'm {self.profile.name}. "
            "I'm not fully configured yet — please ask an administrator to set up the AI connector."
        )


class RuleBasedAgentSession(AgentSession):
    """
    Simple pattern-matching chat for dev/demo — no API key required.
    Handles greetings, time, echo, basic arithmetic, and help.
    """

    _HELP = (
        "I'm a rule-based bot. I understand:\n"
        "  hello / hi        — greeting\n"
        "  time / date       — current date & time\n"
        "  echo <text>       — repeat your text\n"
        "  calc <expr>       — evaluate simple arithmetic (e.g. calc 2+2*3)\n"
        "  who are you       — my identity\n"
        "  help / ?          — this list\n"
        "  bye               — farewell\n"
        "\nFor real AI, configure an OpenAI connector in Admin → Steven."
    )

    def invoke(self, user_prompt: str) -> str:
        import ast
        import operator as op
        from datetime import datetime

        text = user_prompt.strip()
        lower = text.lower()

        # Greeting
        if any(lower.startswith(w) for w in ("hello", "hi", "hey", "greetings", "good morning", "good evening")):
            return f"Hello! I'm {self.profile.name}. How can I help? (type 'help' to see what I can do)"

        # Farewell
        if any(lower.startswith(w) for w in ("bye", "goodbye", "see you", "ciao", "later")):
            return f"Goodbye! Come back anytime."

        # Identity
        if any(p in lower for p in ("who are you", "what are you", "your name", "introduce yourself")):
            return (
                f"I'm {self.profile.name}, a rule-based chat assistant running inside Toto Studio.\n"
                f"System prompt: {self.profile.system_prompt[:120]}…\n\n"
                "I don't call any external AI — I just match patterns. "
                "Configure an OpenAI connector to get real responses."
            )

        # Help
        if lower in ("help", "?", "commands", "what can you do", "usage"):
            return self._HELP

        # Current time / date
        if any(p in lower for p in ("time", "date", "today", "now", "clock")):
            now = datetime.now()
            return f"Current date & time: {now.strftime('%A, %d %B %Y — %H:%M:%S')}"

        # Echo
        if lower.startswith("echo "):
            return text[5:].strip() or "(nothing to echo)"

        # Calculator
        if lower.startswith(("calc ", "calculate ", "compute ", "= ")):
            expr = text.split(None, 1)[1] if " " in text else ""
            return self._safe_eval(expr)

        # Bare arithmetic expression (no keyword)
        if self._looks_like_math(lower):
            return self._safe_eval(text)

        # Default
        return (
            f'I didn\'t understand: "{text}"\n'
            "Type 'help' to see what I can do, "
            "or configure an OpenAI connector for real AI responses."
        )

    @staticmethod
    def _looks_like_math(text: str) -> bool:
        import re
        return bool(re.fullmatch(r"[\d\s\+\-\*\/\%\(\)\.]+", text.strip()))

    @staticmethod
    def _safe_eval(expr: str) -> str:
        import ast
        import operator as op

        _OPS = {
            ast.Add: op.add, ast.Sub: op.sub,
            ast.Mult: op.mul, ast.Div: op.truediv,
            ast.Mod: op.mod, ast.Pow: op.pow,
            ast.UAdd: op.pos, ast.USub: op.neg,
        }

        def _eval(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return node.value
            if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
                return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
            if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
                return _OPS[type(node.op)](_eval(node.operand))
            raise ValueError(f"Unsupported operation: {ast.dump(node)}")

        try:
            tree = ast.parse(expr.strip(), mode="eval")
            result = _eval(tree.body)
            if isinstance(result, float) and result.is_integer():
                result = int(result)
            return f"{expr.strip()} = {result}"
        except Exception:
            return f'Could not evaluate: "{expr}". Try something like: calc 2 + 3 * 4'


def create_agent_session(profile) -> AgentSession:
    """Return the right session implementation for an agent profile."""

    if profile.connector is None:
        return StubAgentSession(
            profile,
            reason="No connector is configured for this agent.",
        )

    if profile.connector.provider == "rule_based":
        return RuleBasedAgentSession(profile)

    if not profile.connector.is_active:
        return StubAgentSession(profile, reason="Connector is inactive.")

    if find_spec("langchain") is None:
        return StubAgentSession(
            profile,
            reason="LangChain is not installed in this environment.",
        )

    return RealAgentSession(profile)


def run_agent(agent_run):
    """
    Backwards-compatible helper.

    Existing code can still call:

        run_agent(agent_run)

    New code can use:

        create_agent_session(agent).run(agent_run)
    """
    return create_agent_session(agent_run.agent).run(agent_run)