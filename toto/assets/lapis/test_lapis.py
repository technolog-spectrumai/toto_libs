from lapis.compiler import LapisCompiler
from lapis.executor import LapisContext, LapisExecutor
from lapis.loader import loads_contract, dumps_contract


def test_json_yaml_roundtrip():
    tree = {"language":"lapis","version":1,"name":"T","actions":{"noop":{"type":"seq","steps":[]}}}
    text = dumps_contract(tree, fmt="json")
    assert loads_contract(text, fmt="json")["language"] == "lapis"


def test_compile_and_execute_record():
    tree = {
        "language": "lapis",
        "version": 1,
        "name": "RecordOnly",
        "actions": {"run": {"type": "record", "kind": "hello", "data": {"x": {"type": "int", "value": 1}}}},
    }
    plan = LapisCompiler().compile_action(tree, "run")
    events = []
    ctx = LapisContext(
        agreement=object(),
        params={},
        state={},
        accounts={},
        assets={},
        metadata={},
        transfer=lambda **kw: kw,
        create_obligation=lambda **kw: kw,
        record=lambda **kw: events.append(kw) or kw,
        balance=lambda **kw: 0,
    )
    LapisExecutor().execute(plan, ctx)
    assert events[0]["kind"] == "hello"
    assert events[0]["data"]["x"] == 1
