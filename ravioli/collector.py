import datetime
from decimal import Decimal
from django.db.models.fields.related import ForeignKey, ManyToManyField
from django.core import serializers
from ravioli.models import (
    Graph,
    CollectionType,
    DataNode,
    RelationType,
    DataEdge,
)
from django.apps import apps



class CollectorHelper:
    """
    Instance-based helper for converting Django models into graph structures.
    Stores:
      - self.graph
      - self.node_lookup
    """

    DJANGO_TO_JSON = {
        # Strings
        "CharField": "string",
        "TextField": "text",  # textarea
        "EmailField": "string",
        "URLField": "string",
        "UUIDField": "string",
        "SlugField": "string",
        "GenericIPAddressField": "string",

        # Numbers
        "IntegerField": "integer",
        "BigIntegerField": "integer",
        "SmallIntegerField": "integer",
        "PositiveIntegerField": "integer",
        "PositiveSmallIntegerField": "integer",
        "AutoField": "integer",
        "BigAutoField": "integer",

        "FloatField": "number",
        "DecimalField": "number",

        # Boolean
        "BooleanField": "boolean",
        "NullBooleanField": "boolean",

        # Dates / Times
        "DateField": "date",
        "DateTimeField": "datetime",
        "TimeField": "time",
        "DurationField": "string",  # ISO 8601 duration

        # JSON / Array
        "JSONField": "object",
        "ArrayField": "array",

        # Files / Images
        "FileField": "string",  # URL
        "ImageField": "string",  # URL

        # Choice fields (detected separately)
        "ChoiceField": "choice",
    }

    def __init__(self, graph):
        self.graph = graph
        self.node_lookup = {}

    # ---------------------------------------------------------
    # STATIC HELPERS (pure functions)
    # ---------------------------------------------------------
    @staticmethod
    def normalize_value(value):
        if isinstance(value, (datetime.date, datetime.datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (list, tuple)):
            return [CollectorHelper.normalize_value(v) for v in value]
        if isinstance(value, dict):
            return {k: CollectorHelper.normalize_value(v) for k, v in value.items()}
        return value

    @staticmethod
    def create_collection_type(model):
        """
        Create a CollectionType with auto-generated JSON schema and form layout
        based on the Django model fields.
        """

        name = getattr(model, "graph_node_type", model.__name__)
        if name is None:
            return None
        app_label = model._meta.app_label
        model_path = f"{app_label}.{model.__name__}"

        # Build JSON schema + form layout
        properties = {}
        required = []
        form_fields = []

        for field in model._meta.get_fields():
            # Skip relations
            if field.is_relation:
                continue
            if field.primary_key:
                continue
            field_type = field.get_internal_type()
            json_type = CollectorHelper.DJANGO_TO_JSON.get(field_type, "string")

            properties[field.name] = {"type": json_type}

            if not field.null and not field.blank:
                required.append(field.name)

            form_fields.append({
                "name": field.name,
                "type": json_type,
                "label": field.verbose_name.title(),
                "required": not field.blank,
            })

        json_schema = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        form_layout = {
            "fields": form_fields
        }

        ct, created = CollectionType.objects.get_or_create(
            model_name=model_path,
            defaults={
                "name": name,
                "json_schema": json_schema,
                "form_layout": form_layout,
            },
        )

        # If it already exists but is empty, enrich it
        if not created:
            updated = False

            if not ct.json_schema:
                ct.json_schema = json_schema
                updated = True

            if not ct.form_layout:
                ct.form_layout = form_layout
                updated = True

            if updated:
                ct.save()

        return ct

    @staticmethod
    def create_relation_type(model, field, prefix):
        default_name = f"{prefix}:{model.__name__}->{field.related_model.__name__}"
        rel_name = getattr(field, "db_comment", "") or default_name
        relation_type, _ = RelationType.objects.get_or_create(name=rel_name)
        return relation_type

    def create_node(self, model, obj, collection_type):
        serialized = serializers.serialize("python", [obj])[0]
        fields = serialized["fields"]
        normalized = CollectorHelper.normalize_value(fields)

        node = DataNode.objects.create(
            name=f"{model.__name__}-{obj.pk}",
            data=normalized,
            collection_type=collection_type,
            graph=self.graph,
        )

        self.node_lookup[(model, obj.pk)] = node
        return node

    def create_fk_edges(self, model, obj, node):
        for field in model._meta.get_fields():
            if not isinstance(field, ForeignKey):
                continue

            # Skip ContentType and any FK without db_comment
            if str(field).find("asset") != -1:
                i = 0
            rel_name = getattr(field, "db_comment", None)
            if not rel_name:
                continue

            target_obj = getattr(obj, field.name, None)
            if not target_obj:
                continue

            target_node = self.node_lookup.get((field.related_model, target_obj.pk))
            if not target_node:
                continue

            # Lookup only — do NOT create
            relation_type = RelationType.objects.filter(name=rel_name).first()
            if not relation_type:
                continue

            DataEdge.objects.get_or_create(
                source=node,
                target=target_node,
                relation_type=relation_type,
                graph=self.graph,
            )

    def create_m2m_edges(self, model, obj, node):
        for field in model._meta.get_fields():
            if not isinstance(field, ManyToManyField):
                continue

            # Skip if no db_comment
            rel_name = getattr(field, "db_comment", None)
            if not rel_name:
                continue

            # Lookup only — do NOT create
            relation_type = RelationType.objects.filter(name=rel_name).first()
            if not relation_type:
                continue

            for target_obj in getattr(obj, field.name).all():
                target_node = self.node_lookup.get((field.related_model, target_obj.pk))
                if not target_node:
                    continue

                DataEdge.objects.get_or_create(
                    source=node,
                    target=target_node,
                    relation_type=relation_type,
                    graph=self.graph,
                )

    def get_node(self, model, pk):
        return self.node_lookup.get((model, pk))

    @staticmethod
    def build_types(collector):
        app_label = collector.app_name
        app_config = apps.get_app_config(app_label)

        for model in app_config.get_models():

            # 1. Create CollectionType
            ct = CollectorHelper.create_collection_type(model)

            # 2. Create RelationTypes ONLY if db_comment is present
            for field in model._meta.get_fields():

                # ForeignKey
                if isinstance(field, ForeignKey):
                    rel_name = getattr(field, "db_comment", None)
                    if rel_name:  # only create if explicitly defined
                        CollectorHelper.create_relation_type(model, field, prefix="FK")

                # ManyToMany
                if isinstance(field, ManyToManyField):
                    rel_name = getattr(field, "db_comment", None)
                    if rel_name:
                        CollectorHelper.create_relation_type(model, field, prefix="M2M")

        return app_label

    def create_nodes_for_model(self, model):
        model_path = f"{model._meta.app_label}.{model.__name__}"

        # Look up type fresh from DB
        collection_type = CollectionType.objects.filter(
            model_name=model_path
        ).first()

        if not collection_type:
            # Skip models without types
            return

        for obj in model.objects.all():
            self.create_node(model, obj, collection_type)

    def create_edges_for_model(self, model):
        for obj in model.objects.all():
            node = self.get_node(model, obj.pk)
            self.create_fk_edges(model, obj, node)
            self.create_m2m_edges(model, obj, node)

    @staticmethod
    def build_graph(collectors, graph):
        """
        Build graph using previously created CollectionTypes.
        If a model has no CollectionType, skip it.
        """

        if graph is None:
            raise ValueError("A graph instance is required.")

        helper = CollectorHelper(graph)

        for collector in collectors:
            app_label = collector.app_name
            app_config = apps.get_app_config(app_label)
            for model in app_config.get_models():
                helper.create_nodes_for_model(model)

        for collector in collectors:
            app_label = collector.app_name
            app_config = apps.get_app_config(app_label)
            for model in app_config.get_models():
                helper.create_edges_for_model(model)

        return helper

