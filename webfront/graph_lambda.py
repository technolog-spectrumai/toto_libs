import networkx as nx
from toto.executor import RestrictedPythonExecutor
import numpy as np


class GraphLambdaHelper:
    """
    Pure static helper for running Cypher queries and converting results.
    No state, no instances, no loading.
    """

    @staticmethod
    def apply_user_lambda(code, graph, name="cypher_lambda"):
        if not code:
            return graph

        # Prepare context
        context = {"G": graph}

        executor = RestrictedPythonExecutor(
            code=code,
            context=context,
            name=name
        )
        result = {}
        try:
            result = executor.execute(extra_globals={ "nx": nx, "np": np } )
        except Exception as e:
            result["error"] = str(e)

        return result


    @staticmethod
    def apply_graph_lambda(code, graph, name="cypher_lambda"):
        result = GraphLambdaHelper.apply_user_lambda(code, graph, name)

        # If executor returned an error dict → bail out
        if isinstance(result, dict) and "error" in result:
            return graph

        # Enforce graph-only return
        if not isinstance(result, nx.Graph):
            return graph

        return result

