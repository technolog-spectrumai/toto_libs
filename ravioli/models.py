from typing import List
from django.contrib.auth.models import User
from toto.models import SerializableModel
import os
import sys
from io import StringIO
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.management import call_command
from django.apps import apps



class CypherQuery(SerializableModel):
    """
    Represents a predefined Cypher query that can be reused in the graph explorer.
    """

    name = models.CharField(
        max_length=200,
        unique=True,
        help_text="Human-readable name of the query (e.g. 'All Nodes and Relationships')."
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description of what this query does."
    )
    query = models.TextField(
        help_text="The Cypher query string to run against Neo4j."
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cypher_queries",
        help_text="User who created this query."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @staticmethod
    def for_node_types(node_types: List[str], created_by: User = None) -> "CypherQuery":
        """
        Factory method: build a CypherQuery that fetches all nodes of given types
        and their one-hop neighbours.
        """
        if not node_types:
            raise ValueError("You must provide at least one node type.")

        # Build label string like ":Person|Company|Product"
        label_expr = ":`" + "`|:`".join(node_types) + "`"

        cypher = f"""
        MATCH (n{label_expr})-[r]-(m)
        RETURN n, r, m
        LIMIT 100
        """

        return CypherQuery(
            name=f"{', '.join(node_types)} with one-hop neighbours",
            description=f"Fetch all {', '.join(node_types)} nodes and their immediate neighbours.",
            query=cypher.strip(),
            created_by=created_by,
            is_active=True,
        )


_GRAPH_ALLOWED_APPS = getattr(settings, "GRAPH_APPS", [])


class GraphSync(models.Model):

    class SyncCommandError(Exception):
        """Base class for sync command errors."""

    class SyncCommandNotFound(SyncCommandError):
        """Raised when the sync command file is missing."""

    class SyncCommandExecutionFailed(SyncCommandError):
        """Raised when the command execution throws an error."""

    GRAPH_ALLOWED_APPS = _GRAPH_ALLOWED_APPS
    app_name = models.CharField(
        max_length=64,
        choices=[(app, app) for app in _GRAPH_ALLOWED_APPS],
        help_text="Target app for graph sync"
    )
    scheduled_at = models.DateTimeField(
        default=timezone.now,
        help_text="When this sync is scheduled"
    )
    executed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this sync was actually executed"
    )

    def __str__(self):
        return f"GraphSync for {self.app_name} scheduled at {self.scheduled_at}, executed at {self.executed_at or 'pending'}"

    def run_sync_command(self):
        """
        Runs the 'sync' management command for the specified app.
        Raises:
            SyncCommandNotFound: if the command file doesn't exist
            SyncCommandExecutionFailed: if execution fails
        """
        app_config = apps.get_app_config(self.app_name)
        cmd_path = os.path.join(
            app_config.path,
            "management",
            "commands",
            f"sync_{self.app_name}.py"
        )

        if not os.path.isfile(cmd_path):
            raise self.SyncCommandNotFound(f"No sync command found for '{self.app_name}'")

        try:
            out = StringIO()
            call_command(
                f"sync_{self.app_name}",
                stdout=out,
                stderr=out
            )
            output = out.getvalue()
            sys.stdout.write(output)

            # mark execution time
            self.executed_at = timezone.now()
            self.save(update_fields=["executed_at"])

        except Exception as e:
            raise self.SyncCommandExecutionFailed(
                f"Error running sync for '{self.app_name}': {str(e)}"
            )


