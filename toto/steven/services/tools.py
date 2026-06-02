from __future__ import annotations

from datetime import datetime, timezone
from langchain_core.tools import tool

from .graph_rag import build_graph_cypher_qa_chain, langchain_neo4j_available


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


def _chain_answer(response):
    if isinstance(response, dict):
        for key in ("result", "answer", "output"):
            if key in response:
                return str(response[key])
    return str(response)


def graph_cypher_qa_tool(llm):
    chain_cache = {}

    @tool("graph_cypher_qa")
    def graph_cypher_qa(question: str) -> str:
        """Answer questions by generating read-only Cypher against Toto's Neo4j graph."""
        if llm is None:
            return "Graph Cypher QA requires a real LangChain chat model."
        try:
            if "chain" not in chain_cache:
                chain_cache["chain"] = build_graph_cypher_qa_chain(llm)
        except Exception as exc:
            return f"Graph Cypher QA error: {exc}"

        chain = chain_cache["chain"]
        try:
            return _chain_answer(chain.invoke({"query": question}))
        except TypeError:
            try:
                return _chain_answer(chain.invoke(question))
            except Exception as exc:
                return f"Graph Cypher QA error: {exc}"
        except Exception as exc:
            return f"Graph Cypher QA error: {exc}"

    return graph_cypher_qa


def tools_for_agent(agent_profile, llm=None):
    enabled_keys = list(agent_profile.tools.filter(enabled=True).values_list('key', flat=True))
    tools = [TOOL_REGISTRY[key] for key in enabled_keys if key in TOOL_REGISTRY]
    if "graph_cypher_qa" in enabled_keys and langchain_neo4j_available():
        tools.append(graph_cypher_qa_tool(llm))
    return tools
