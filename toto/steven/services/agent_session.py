from __future__ import annotations

from abc import ABC, abstractmethod

from django.utils import timezone


def _run_tool_loop(model, tools, system_prompt: str, user_prompt: str, history, max_iters: int = 10) -> str:
    """Tool-calling loop using only langchain_core primitives.

    Works with any LangChain version that supports tool calling — no
    AgentExecutor or create_tool_calling_agent required.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    tool_map = {t.name: t for t in (tools or [])}
    bound = model.bind_tools(list(tool_map.values())) if tool_map else model

    messages = [SystemMessage(content=system_prompt)]
    for m in (history or []):
        cls = HumanMessage if m["role"] == "user" else AIMessage
        messages.append(cls(content=m["content"]))
    messages.append(HumanMessage(content=user_prompt))

    for _ in range(max_iters):
        response = bound.invoke(messages)
        messages.append(response)

        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            content = response.content
            if isinstance(content, list):
                return "\n".join(str(c) for c in content)
            return str(content) if content else ""

        for tc in calls:
            tool = tool_map.get(tc["name"])
            try:
                result = tool.invoke(tc["args"]) if tool else f"Unknown tool: {tc['name']}"
            except Exception as exc:
                result = f"Tool error: {exc}"
            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    return str(getattr(messages[-1], "content", "")) or "(max iterations reached)"


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
            tools = tools_for_agent(self.profile, llm=model)

        self._last_token_usage = None
        return _run_tool_loop(
            model, tools,
            system_prompt=self.profile.system_prompt or "",
            user_prompt=user_prompt,
            history=history,
        )


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


class OllamaAgentSession(AgentSession):
    """LangChain-Ollama-backed agent session using a local Ollama instance."""

    def invoke(self, user_prompt: str, history=None) -> str:
        from django.conf import settings
        from langchain_ollama import ChatOllama

        from .tools import tools_for_agent

        if not self.profile.is_active:
            raise RuntimeError(f'Agent "{self.profile.name}" is inactive.')

        from toto.vicuna.chat import resolve_ollama_chat_model

        host = (
            getattr(self.profile.connector, "base_url", None)
            or getattr(settings, "VICUNA_OLLAMA_HOST", "http://localhost:11434")
        )
        model = ChatOllama(
            model=resolve_ollama_chat_model(self.profile),
            base_url=host,
            temperature=(
                self.profile.temperature
                if self.profile.temperature is not None
                else getattr(settings, "VICUNA_CHAT_TEMPERATURE", 0.1)
            ),
            timeout=getattr(settings, "VICUNA_CHAT_TIMEOUT", 180),
        )

        self._last_token_usage = None
        return _run_tool_loop(
            model, tools_for_agent(self.profile, llm=model),
            system_prompt=self.profile.system_prompt or "",
            user_prompt=user_prompt,
            history=history,
        )


def create_agent_session(profile) -> AgentSession:
    """Return the right AgentSession for *profile* by delegating to the provider registry.

    Pre-conditions checked here (apply to every provider):
    - connector must exist and be active
    Provider-specific logic (availability checks, session class) lives in
    the strategy registered in services/provider_strategies.py.
    """
    if profile.connector is None:
        return StubAgentSession(profile, reason="No connector is configured for this agent.")

    if not profile.connector.is_active:
        return StubAgentSession(profile, reason="Connector is inactive.")

    from toto.steven.services.provider_strategies import REGISTRY
    strategy = REGISTRY.get(profile.connector.provider)
    if strategy is None:
        return StubAgentSession(
            profile,
            reason=f"Unknown provider {profile.connector.provider!r}. "
                   f"Supported: {', '.join(REGISTRY)}.",
        )

    return strategy.create_session(profile)


def run_agent(agent_run):
    """
    Backwards-compatible helper.

    Existing code can still call:

        run_agent(agent_run)

    New code can use:

        create_agent_session(agent).run(agent_run)
    """
    return create_agent_session(agent_run.agent).run(agent_run)
