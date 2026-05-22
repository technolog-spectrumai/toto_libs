from collections import defaultdict, deque

from ..models import Workflow, WorkflowNode


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class WorkflowValidator:

    def validate(self, workflow: Workflow) -> None:
        errors: list[str] = []

        nodes = list(workflow.nodes.all())
        edges = list(workflow.edges.select_related("source", "target").all())

        if not nodes:
            errors.append("Workflow has no nodes.")
            raise ValidationError(errors)

        node_ids = {n.id for n in nodes}
        self._validate_nodes(nodes, errors)
        self._validate_edges(edges, node_ids, errors)
        self._validate_split_constraints(nodes, edges, errors)
        self._validate_acyclic(node_ids, edges, errors)

        if errors:
            raise ValidationError(errors)

    def _validate_nodes(self, nodes: list, errors: list[str]) -> None:
        for node in nodes:
            if node.node_type == WorkflowNode.LAMBDA:
                if node.lambda_function_id is None:
                    errors.append(
                        f"Lambda node {node.id} ({node.label!r}) has no lambda_function."
                    )
            elif node.node_type == WorkflowNode.REPORT:
                if node.report_template_id is None:
                    errors.append(
                        f"Report node {node.id} ({node.label!r}) has no report_template."
                    )

    def _validate_edges(self, edges: list, node_ids: set, errors: list[str]) -> None:
        seen: set[tuple] = set()
        for edge in edges:
            if edge.source_id == edge.target_id:
                errors.append(f"Edge {edge.id} is a self-loop on node {edge.source_id}.")
            pair = (edge.source_id, edge.target_id)
            if pair in seen:
                errors.append(
                    f"Duplicate edge from node {edge.source_id} to {edge.target_id}."
                )
            seen.add(pair)
            if edge.source_id not in node_ids:
                errors.append(f"Edge {edge.id} source node {edge.source_id} not in workflow.")
            if edge.target_id not in node_ids:
                errors.append(f"Edge {edge.id} target node {edge.target_id} not in workflow.")

    def _validate_split_constraints(self, nodes: list, edges: list, errors: list[str]) -> None:
        incoming_count: dict[int, int] = defaultdict(int)
        outgoing: dict[int, list] = defaultdict(list)
        for edge in edges:
            incoming_count[edge.target_id] += 1
            outgoing[edge.source_id].append(edge)

        for node in nodes:
            if node.node_type != WorkflowNode.SPLIT:
                continue
            if incoming_count[node.id] != 1:
                errors.append(
                    f"Split node {node.id} must have exactly one incoming edge "
                    f"(found {incoming_count[node.id]})."
                )
            node_outgoing = outgoing[node.id]
            if not node_outgoing:
                errors.append(f"Split node {node.id} has no outgoing edges.")
            default_count = sum(1 for e in node_outgoing if e.is_default)
            if default_count > 1:
                errors.append(
                    f"Split node {node.id} has {default_count} default edges (max 1)."
                )

    def _validate_acyclic(self, node_ids: set, edges: list, errors: list[str]) -> None:
        in_degree: dict[int, int] = {nid: 0 for nid in node_ids}
        adjacency: dict[int, list[int]] = defaultdict(list)

        for edge in edges:
            if edge.source_id in in_degree and edge.target_id in in_degree:
                in_degree[edge.target_id] += 1
                adjacency[edge.source_id].append(edge.target_id)

        queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
        visited = 0
        while queue:
            nid = queue.popleft()
            visited += 1
            for neighbor in adjacency[nid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited < len(node_ids):
            errors.append("Workflow graph contains a cycle.")
