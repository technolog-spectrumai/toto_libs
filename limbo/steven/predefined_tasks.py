"""Steven predefined workflow tasks."""
from toto.workflows.predefined_tasks import register


@register("steven_run_agent")
def steven_run_agent(input_data: dict) -> dict:
    """Run an AgentRun inside a workflow node.

    Expected input_data: {"data": {"agent_run_pk": int}}
    """
    from toto.steven.models import AgentRun
    from toto.steven.services.agent_session import create_agent_session

    data = input_data.get("data") or {}
    agent_run_pk = data.get("agent_run_pk")
    if agent_run_pk is None:
        raise ValueError("steven_run_agent requires agent_run_pk in input data.")

    agent_run = AgentRun.objects.select_related("agent__connector").get(pk=agent_run_pk)
    create_agent_session(agent_run.agent).run(agent_run)

    return {
        "data": {
            "agent_run_pk": agent_run.pk,
            "status": agent_run.status,
            "result": agent_run.result,
            "error": agent_run.error,
        }
    }
