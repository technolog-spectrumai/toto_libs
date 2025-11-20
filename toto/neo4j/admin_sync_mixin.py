from django.contrib import messages

class Neo4jSyncMixin:
    """
    Mixin to add a reusable admin action for syncing data
    into Neo4j using a given strategy class.
    """

    # subclasses must set this
    strategy_class = None
    strategy_label = None  # optional human-friendly label

    def sync_with_neo4j(self, request, queryset):
        """
        Run the configured strategy_class on the queryset.
        """
        if not self.strategy_class:
            self.message_user(request, "No strategy_class defined.", level=messages.ERROR)
            return

        label = self.strategy_label or self.strategy_class.__name__
        try:
            self.message_user(request, f"Syncing {label} with Neo4j...")
            strategy = self.strategy_class()
            strategy.execute(queryset)
            self.message_user(request, f"{label} synced successfully to Neo4j.")
        except Exception as e:
            self.message_user(request, f"Error syncing {label}: {str(e)}", level=messages.ERROR)
