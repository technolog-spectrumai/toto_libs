from toto.workflows.predefined_tasks import register


@register("fileservice_run")
def fileservice_run(input_data: dict) -> dict:
    """
    Workflow node entrypoint for a file service.

    Expects input_data = {"data": {"run_id": N}}.
    """
    from .runner import execute_run

    data = input_data.get("data") or {}
    run_id = data.get("run_id")
    if run_id is None:
        raise ValueError("fileservice_run requires run_id in input data.")
    result = execute_run(run_id)
    return {"data": result}
