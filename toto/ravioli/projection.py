from django.conf import settings
from django.utils.module_loading import import_string


class ProjectionRegistry:

    def __init__(self):
        self.projections = [
            import_string(path)()   # dynamically load class and instantiate
            for path in settings.GRAPH_PROJECTIONS
        ]

    def grouped_models(self):
        groups = {}
        for proj in self.projections:
            groups.setdefault(proj.app, []).append(proj.model)
        return groups

    def find_projections_for(self, selected_models):
        return [
            proj for proj in self.projections
            if proj.model in selected_models
        ]


class ProjectionRunner:
    def __init__(self, selected_models=None):
        registry = ProjectionRegistry()

        if selected_models:
            self.projections = registry.find_projections_for(selected_models)
        else:
            self.projections = registry.projections

    def projection_stats(self, proj):
        if hasattr(proj, "projection_stats"):
            stats = proj.projection_stats()
        else:
            stats = {
                "items": 0,
                "links": 0,
                "node_data_size": 1,
            }

        return {
            "items": int(stats.get("items") or 0),
            "links": int(stats.get("links") or 0),
            "node_data_size": int(stats.get("node_data_size") or 0),
        }

    def projection_size(self, stats):
        node_size = stats["items"] * stats["node_data_size"]
        return node_size + stats["links"]

    def projection_plan(self):
        plan = []

        for proj in self.projections:
            stats = self.projection_stats(proj)
            node_size = stats["items"] * stats["node_data_size"]
            edge_size = stats["links"]

            plan.append({
                "projection": proj,
                "stats": stats,
                "node_size": node_size,
                "edge_size": edge_size,
                "total_size": node_size + edge_size,
            })

        return plan

    def run(self):
        for proj in self.projections:
            proj.sync_nodes()

        for proj in self.projections:
            proj.sync_edges()

    def run_with_progress(self):
        plan = self.projection_plan()
        total_size = sum(item["total_size"] for item in plan)
        current_size = 0

        yield {
            "status": "started",
            "current": 0,
            "total": total_size,
            "message": "Starting projection sync",
        }

        for item in plan:
            proj = item["projection"]

            proj.sync_nodes()
            current_size += item["node_size"]
            yield {
                "status": "running",
                "phase": "nodes",
                "projection": proj.__class__.__name__,
                "model": str(proj.model),
                "current": current_size,
                "total": total_size,
                "items": item["stats"]["items"],
                "links": item["stats"]["links"],
                "node_data_size": item["stats"]["node_data_size"],
                "message": f"Synced nodes for {proj.__class__.__name__}",
            }

        for item in plan:
            proj = item["projection"]

            proj.sync_edges()
            current_size += item["edge_size"]
            yield {
                "status": "running",
                "phase": "edges",
                "projection": proj.__class__.__name__,
                "model": str(proj.model),
                "current": current_size,
                "total": total_size,
                "items": item["stats"]["items"],
                "links": item["stats"]["links"],
                "node_data_size": item["stats"]["node_data_size"],
                "message": f"Synced edges for {proj.__class__.__name__}",
            }

        yield {
            "status": "completed",
            "current": total_size,
            "total": total_size,
            "message": "Projection sync completed",
        }
