from .adapter import Adapter


class EventAdapter(Adapter):
    app_label = "events"   # or whatever your Django app label is

    # ---------------------------------------------------------
    # 1. COLLECTION (NODE) TYPES
    # ---------------------------------------------------------
    def build_collection_types(self):
        return [
            {
                "name": "Event",
                "json_schema": None,
                "form_layout": None,
            },
            {
                "name": "EventCategory",
                "json_schema": None,
                "form_layout": None,
            },
            {
                "name": "User",
                "json_schema": None,
                "form_layout": None,
            },
        ]

    # ---------------------------------------------------------
    # 2. RELATION (EDGE) TYPES
    # ---------------------------------------------------------
    def build_relation_types(self):
        return [
            {
                "name": "OrganizedBy",
                "metadata": None,
            },
            {
                "name": "CategorizedAs",
                "metadata": None,
            },
        ]

    # ---------------------------------------------------------
    # 3. BUILD NODES
    # ---------------------------------------------------------
    def build_nodes(self, extracted_app_data):
        nodes = []

        # EventCategory nodes
        for obj in extracted_app_data.get("EventCategory", []):
            pk = obj["pk"]
            fields = obj["fields"]

            nodes.append({
                "id": f"EventCategory:{pk}",
                "type": "EventCategory",
                "name": fields.get("name", pk),
                "data": fields,
            })

        # Event nodes
        for obj in extracted_app_data.get("Event", []):
            pk = obj["pk"]
            fields = obj["fields"]

            nodes.append({
                "id": f"Event:{pk}",
                "type": "Event",
                "name": fields.get("title", pk),
                "data": fields,
            })

        # Organizer (User) nodes
        # We only create nodes for users referenced by events
        user_ids = set()
        for obj in extracted_app_data.get("Event", []):
            organizer_id = obj["fields"].get("organizer")
            if organizer_id:
                user_ids.add(organizer_id)

        for obj in extracted_app_data.get("User", []):
            if obj["pk"] in user_ids:
                pk = obj["pk"]
                fields = obj["fields"]

                nodes.append({
                    "id": f"User:{pk}",
                    "type": "User",
                    "name": fields.get("username", pk),
                    "data": fields,
                })

        return nodes

    # ---------------------------------------------------------
    # 4. BUILD EDGES
    # ---------------------------------------------------------
    def build_edges(self, extracted_app_data):
        edges = []

        # Event → User (organizer)
        for obj in extracted_app_data.get("Event", []):
            pk = obj["pk"]
            fields = obj["fields"]

            organizer_id = fields.get("organizer")
            if organizer_id:
                edges.append({
                    "id": f"Event:{pk}->User:{organizer_id}",
                    "type": "OrganizedBy",
                    "source": f"Event:{pk}",
                    "target": f"User:{organizer_id}",
                    "label": "organizer",
                    "metadata": {},
                })

        # Event → EventCategory (category)
        for obj in extracted_app_data.get("Event", []):
            pk = obj["pk"]
            fields = obj["fields"]

            category_id = fields.get("category")
            if category_id:
                edges.append({
                    "id": f"Event:{pk}->EventCategory:{category_id}",
                    "type": "CategorizedAs",
                    "source": f"Event:{pk}",
                    "target": f"EventCategory:{category_id}",
                    "label": "category",
                    "metadata": {},
                })

        return edges
