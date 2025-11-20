from django.contrib import admin
from django.utils.html import format_html
from neo4j import GraphDatabase
from django.conf import settings
from .models import CypherQuery

# Use connection details from settings.py
driver = GraphDatabase.driver(
    f"bolt://{settings.NEO4J_HOST}:{settings.NEO4J_PORT}",
    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
)

def test_cypher_query(query: str) -> bool:
    """
    Try running the Cypher query against Neo4j.
    Returns True if it executes successfully, False otherwise.
    """
    try:
        with driver.session() as session:
            session.run(query).consume()
        return True
    except Exception:
        return False


@admin.register(CypherQuery)
class CypherQueryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "created_by",
        "created_at",
        "is_active",
        "query_valid",   # new column
    )
    list_filter = ("is_active", "created_by")
    search_fields = ("name", "description", "query")
    ordering = ("name",)
    readonly_fields = ("created_at",)

    fieldsets = (
        (None, {
            "fields": ("name", "description", "query", "is_active")
        }),
        ("Metadata", {
            "fields": ("created_by", "created_at"),
            "classes": ("collapse",),
        }),
    )

    def query_valid(self, obj):
        """Run the query and show green/red Yes/No."""
        if test_cypher_query(obj.query):
            return format_html('<span style="color: green; font-weight: bold;">Yes</span>')
        return format_html('<span style="color: red; font-weight: bold;">No</span>')

    query_valid.short_description = "Valid?"
