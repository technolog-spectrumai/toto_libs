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
        response = (
            "Stub agent response.\n\n"
            f"Agent: {self.profile.name}\n"
            f"Prompt: {user_prompt}"
        )

        if self.reason:
            response = f"{response}\n\nReason: {self.reason}"

        return response


def create_agent_session(profile) -> AgentSession:
    """Return the right session implementation for an agent profile."""

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

    if not profile.connector.is_active:
        return StubAgentSession(profile, reason="Connector is inactive.")

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