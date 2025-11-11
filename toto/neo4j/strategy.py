class ConversionStrategy:
    """Base class for SQL → Graph conversion strategies"""

    def create(self, sql_obj):
        raise NotImplementedError

    def update(self, sql_obj, node):
        raise NotImplementedError

    def delete(self, sql_obj):
        raise NotImplementedError

    def get_node(self, sql_obj):
        """Helper to fetch node by UID"""
        raise NotImplementedError

    def get_all_nodes(self):
        """Return all Neo4j nodes of this type"""
        raise NotImplementedError

    def convert(self, sql_obj):
        """Convenience wrapper: create if missing, else update"""
        node = self.get_node(sql_obj)
        if not node:
            return self.create(sql_obj)
        return self.update(sql_obj, node)

    def cleanup_orphans(self, sql_queryset):
        """Remove Neo4j nodes that no longer exist in SQL"""
        sql_ids = {str(obj.pk) for obj in sql_queryset}
        for node in self.get_all_nodes():
            if node.uid not in sql_ids:
                node.delete()

    def execute(self, sql_queryset):
        """
        Run conversion for all objects in queryset,
        then cleanup orphans.
        """
        for obj in sql_queryset:
            self.convert(obj)
        self.cleanup_orphans(sql_queryset)
