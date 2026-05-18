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
    def invoke(self, user_prompt: str, history=None) -> str:
        """Run the agent and return displayable text.

        history: list of {"role": "user"|"assistant", "content": str} dicts,
        ordered oldest-first. Pass None (default) for single-shot runs.
        """
        raise NotImplementedError


class RealAgentSession(AgentSession):
    """Real LangChain-backed agent session."""

    def _connector_environment(self):
        """Stub — override in subclass or inject vault session to provide env credentials."""
        import contextlib
        return contextlib.nullcontext()

    def invoke(self, user_prompt: str, history=None) -> str:
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

            messages = [(m["role"], m["content"]) for m in (history or [])]
            messages.append(("user", user_prompt))

            response = agent.invoke({"messages": messages})

        return extract_text(response)


class StubAgentSession(AgentSession):
    """Stub session used when the real agent cannot run."""

    def __init__(self, profile, reason: str | None = None):
        super().__init__(profile)
        self.reason = reason

    def invoke(self, user_prompt: str, history=None) -> str:
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
    NLP-powered rule-based chat — no API key required.

    The agent's system_prompt is stored as JSON containing intent rules
    (see nlp.py for the schema).  Built-in commands (time, echo, calc,
    help, who) are always available and checked before NLP matching.
    """

    _BUILTIN_HELP = (
        "Built-in commands (always available):\n"
        "  time / date / now        — current date & time\n"
        "  echo <text>              — repeat your text\n"
        "  calc <expr>              — safe arithmetic  (e.g. calc 2+2*3)\n"
        "  who are you              — agent identity\n"
        "  help                     — this list\n"
        "\nEverything else is matched against intent rules stored in my\n"
        "system prompt (Admin → Steven → Agents → system_prompt JSON)."
    )

    def invoke(self, user_prompt: str, history=None) -> str:
        from .nlp import parse_system_prompt, spacy_available

        text = user_prompt.strip()
        lower = text.lower()

        # --- Built-in commands (prefix / exact match, before NLP) ----------

        if lower in ("help", "?", "commands"):
            matcher, _ = parse_system_prompt(self.profile.system_prompt)
            intents = (
                "\n  ".join(f"• {r.intent}" for r in matcher.rules)
                if matcher and matcher.rules
                else "  (no rules defined)"
            )
            return f"{self._BUILTIN_HELP}\n\nConfigured intents:\n  {intents}"

        if lower in ("who are you", "what are you", "whoami"):
            nlp_note = "spaCy" if spacy_available() else "simple split (spaCy not available)"
            return (
                f"I'm {self.profile.name}, a rule-based assistant inside Toto Studio.\n"
                f"NLP backend: {nlp_note}\n"
                "I match your input against intent rules in my system_prompt JSON.\n"
                "Configure an OpenAI connector to replace me with a real AI."
            )

        if any(p in lower for p in ("what time", "current time", "what date", "today", " date", " time", "clock")):
            from datetime import datetime
            now = datetime.now()
            return f"{now.strftime('%A, %d %B %Y  %H:%M:%S')}"

        if lower.startswith("echo "):
            return text[5:].strip() or "(nothing to echo)"

        if lower.startswith(("calc ", "calculate ", "compute ")):
            expr = text.split(None, 1)[1] if " " in text else ""
            return self._safe_eval(expr)

        if self._looks_like_math(lower):
            return self._safe_eval(text)

        # --- NLP intent matching -------------------------------------------

        matcher, fallback = parse_system_prompt(self.profile.system_prompt)
        if matcher is None:
            return (
                fallback + "\n\n"
                "(system_prompt is plain text — convert to JSON rule set to enable NLP matching)"
            )

        intent, response = matcher.match(text)
        return response

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
            raise ValueError(f"Unsupported: {ast.dump(node)}")

        try:
            tree = ast.parse(expr.strip(), mode="eval")
            result = _eval(tree.body)
            if isinstance(result, float) and result.is_integer():
                result = int(result)
            return f"{expr.strip()} = {result}"
        except Exception:
            return f'Could not evaluate: "{expr}". Try e.g. calc 2 + 3 * 4'


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