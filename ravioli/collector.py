from django.apps import apps
from .models import DataNode, DataEdge, CollectionType, RelationType


class Collector:
    """
    Collector takes a Graph with a JSON config and builds
    nodes and edges according to the rules.
    """

    def __init__(self, graph):
        self.graph = graph
        self.config = graph.conversion_rules or {}

    def run(self):
        """Run both phases: nodes then edges."""
        pass
        # self.create_nodes()
        # self.create_edges()

    def create_nodes(self):
        """Create DataNodes for all configured models."""
        models_cfg = self.config.get("models", {})
        for model in apps.get_models():
            if model.__name__ not in models_cfg:
                continue
            model_cfg = models_cfg[model.__name__]

            try:
                collection_type = CollectionType.objects.get(
                    name=model_cfg["collection_type"]
                )
            except CollectionType.DoesNotExist:
                continue

            for instance in model.objects.all():
                DataNode.objects.get_or_create(
                    name=f"{model.__name__}:{instance.pk}",
                    graph=self.graph,
                    collection_type=collection_type,
                    defaults={"data": self._serialize_instance(instance, model_cfg)}
                )

    def create_edges(self):
        """Create DataEdges for all configured relations."""
        models_cfg = self.config.get("models", {})
        for model in apps.get_models():
            if model.__name__ not in models_cfg:
                continue
            model_cfg = models_cfg[model.__name__]

            for instance in model.objects.all():
                source_name = f"{model.__name__}:{instance.pk}"
                try:
                    source_node = DataNode.objects.get(name=source_name, graph=self.graph)
                except DataNode.DoesNotExist:
                    continue

                for field_name, relation_type_name in model_cfg.get("relations", {}).items():
                    related_obj = getattr(instance, field_name, None)
                    if related_obj is None:
                        continue

                    related_qs = related_obj.all() if hasattr(related_obj, "all") else [related_obj]
                    for target in related_qs:
                        self._create_edge(source_node, f"{target.__class__.__name__}:{target.pk}", relation_type_name)

    def _serialize_instance(self, instance, model_cfg):
        """Serialize configured fields into JSON data."""
        return {f: str(getattr(instance, f, None)) for f in model_cfg.get("fields", [])}

    def _create_edge(self, source, target_name, relation_name):
        """Create edge if target node and relation type exist."""
        try:
            target = DataNode.objects.get(name=target_name, graph=self.graph)
            relation_type = RelationType.objects.get(name=relation_name)
            DataEdge.objects.get_or_create(
                source=source,
                target=target,
                relation_type=relation_type,
                graph=self.graph,
                defaults={"label": relation_name}
            )
        except (DataNode.DoesNotExist, RelationType.DoesNotExist):
            pass
