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


def extract_token_usage(agent_response) -> dict:
    """
    Best-effort extraction of token counts from a LangChain agent response.

    Returns a dict with prompt_tokens, completion_tokens, total_tokens
    (all may be None if unavailable).
    Never raises.
    """
    try:
        messages = agent_response.get("messages", []) if isinstance(agent_response, dict) else []
        for msg in reversed(messages):
            usage = None
            if isinstance(msg, dict):
                usage = msg.get("usage_metadata") or msg.get("response_metadata", {}).get("token_usage")
            else:
                usage = getattr(msg, "usage_metadata", None) or \
                        getattr(msg, "response_metadata", {}).get("token_usage")

            if usage:
                if isinstance(usage, dict):
                    return {
                        "prompt_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                    }
    except Exception:
        pass
    return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}




class AgentSession(ABC):
    """Base interface for running an agent session."""

    def __init__(self, profile):
        self.profile = profile

    def run(self, agent_run):
        """Run and persist an AgentRun using this session implementation."""
        agent_run.status = "running"
        agent_run.started_at = timezone.now()
        agent_run.save(update_fields=["status", "started_at"])

        self._last_token_usage = None
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

        # ── Metering ─────────────────────────────────────────────────────────
        self._record_metering(agent_run, self._last_token_usage)

        return agent_run

    def _record_metering(self, agent_run, token_usage=None):
        """Best-effort metering for agent run and token usage."""
        from toto.metering.utils import safe_record_usage as _m
        _run_pk = str(agent_run.pk)
        _profile = self.profile
        _latency = None
        if agent_run.started_at and agent_run.finished_at:
            _latency = int((agent_run.finished_at - agent_run.started_at).total_seconds() * 1000)

        _src = {
            "source_type": "steven.AgentRun",
            "source_id": _run_pk,
            "source_label": _profile.name if _profile else "",
        }
        # Determine subject from agent_run.agent's owner if available
        _sub = {"subject_type": "system", "subject_id": "steven"}
        if hasattr(agent_run, "user") and agent_run.user_id:
            _sub = {
                "subject_type": "auth.User",
                "subject_id": str(agent_run.user_id),
                "subject_label": str(agent_run.user),
            }

        _meta = {
            "model": getattr(_profile, "model_name", None),
            "status": agent_run.status,
            "latency_ms": _latency,
        }
        if _profile and getattr(_profile, "connector", None):
            _meta["connector_id"] = str(_profile.connector.pk)

        _m(
            metric_code="ai.agent_run",
            quantity=1,
            unit="run",
            idempotency_key=f"steven.agent_run:{_run_pk}",
            metadata=_meta,
            **_src, **_sub,
        )

        if token_usage:
            pt = token_usage.get("prompt_tokens")
            ct = token_usage.get("completion_tokens")
            tt = token_usage.get("total_tokens")
            if pt:
                _m(metric_code="ai.prompt_token", quantity=pt, unit="token",
                   idempotency_key=f"steven.prompt_token:{_run_pk}",
                   metadata=_meta, **_src, **_sub)
            if ct:
                _m(metric_code="ai.completion_token", quantity=ct, unit="token",
                   idempotency_key=f"steven.completion_token:{_run_pk}",
                   metadata=_meta, **_src, **_sub)
            if tt and not (pt or ct):
                _m(metric_code="ai.total_token", quantity=tt, unit="token",
                   idempotency_key=f"steven.total_token:{_run_pk}",
                   metadata=_meta, **_src, **_sub)

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
                tools=tools_for_agent(self.profile, llm=model),
                system_prompt=self.profile.system_prompt or "",
            )

            messages = [(m["role"], m["content"]) for m in (history or [])]
            messages.append(("user", user_prompt))

            response = agent.invoke({"messages": messages})

        # Stash token usage on self for _record_metering to pick up
        self._last_token_usage = extract_token_usage(response)
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


class OllamaAgentSession(AgentSession):
    """LangChain-Ollama-backed agent session using a local Ollama instance."""

    def invoke(self, user_prompt: str, history=None) -> str:
        from django.conf import settings
        from langchain_ollama import ChatOllama

        from .tools import tools_for_agent

        if not self.profile.is_active:
            raise RuntimeError(f'Agent "{self.profile.name}" is inactive.')

        from toto.vicuna.chat import resolve_ollama_chat_model

        model = ChatOllama(
            model=resolve_ollama_chat_model(self.profile),
            base_url=getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434"),
            temperature=(
                self.profile.temperature
                if self.profile.temperature is not None
                else getattr(settings, "VICUNA_CHAT_TEMPERATURE", 0.1)
            ),
            timeout=getattr(settings, "VICUNA_CHAT_TIMEOUT", 180),
        )

        from langchain.agents import create_agent

        agent = create_agent(
            model=model,
            tools=tools_for_agent(self.profile, llm=model),
            system_prompt=self.profile.system_prompt or "",
        )

        messages = [(m["role"], m["content"]) for m in (history or [])]
        messages.append(("user", user_prompt))

        response = agent.invoke({"messages": messages})
        self._last_token_usage = extract_token_usage(response)
        return extract_text(response)


def _ollama_available() -> bool:
    """True when langchain-ollama is installed and importable."""
    return find_spec("langchain_ollama") is not None


def create_agent_session(profile) -> AgentSession:
    """Return the right session implementation for an agent profile.

    Rule-of-thumb: if langchain-ollama is installed, Ollama is the default
    engine for every non-rule-based, active session regardless of which
    connector (or model name) is configured on the profile.
    """
    # Rule-based is always its own thing — no AI model involved.
    if profile.connector is not None and profile.connector.provider == "rule_based":
        return RuleBasedAgentSession(profile)

    # An explicitly inactive connector should still be honoured as a block.
    if profile.connector is not None and not profile.connector.is_active:
        return StubAgentSession(profile, reason="Connector is inactive.")

    # Helpful message when an ollama connector is explicitly configured
    # but the package isn't installed.
    if (
        profile.connector is not None
        and profile.connector.provider == "ollama"
        and not _ollama_available()
    ):
        return StubAgentSession(
            profile,
            reason="langchain-ollama is not installed. Run: pip install langchain-ollama",
        )

    # Ollama is the default engine when installed.
    if _ollama_available():
        return OllamaAgentSession(profile)

    # No Ollama — fall back to OpenAI / LangChain path.
    if profile.connector is None:
        return StubAgentSession(
            profile,
            reason="No connector is configured for this agent.",
        )

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
