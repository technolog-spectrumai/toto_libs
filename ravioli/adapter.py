from abc import ABC, abstractmethod


class Adapter(ABC):
    """
    Abstract adapter for per-app graph conversion.
    Each adapter must implement exactly four methods:
      - build_collection_types()
      - build_relation_types()
      - build_nodes()
      - build_edges()
    """

    app_label: str = None  # must be overridden by subclasses

    # ---------------------------------------------------------
    # TYPE BUILDERS
    # ---------------------------------------------------------
    @abstractmethod
    def build_collection_types(self) -> list[dict]:
        """
        Return a list of node type definitions.
        Each item must be:
            {
                "name": "TypeName",
                "json_schema": {...} or None,
                "form_layout": {...} or None
            }
        """
        pass

    @abstractmethod
    def build_relation_types(self) -> list[dict]:
        """
        Return a list of edge type definitions.
        Each item must be:
            {
                "name": "EdgeTypeName",
                "metadata": {...} or None
            }
        """
        pass

    # ---------------------------------------------------------
    # NODE / EDGE BUILDERS
    # ---------------------------------------------------------
    @abstractmethod
    def build_nodes(self, extracted_app_data: dict) -> list[dict]:
        """
        Convert extracted Django data into a list of node dicts.
        Must return:
            [
                {
                    "id": "...",
                    "type": "...",
                    "name": "...",
                    "data": {...}
                },
                ...
            ]
        """
        pass

    @abstractmethod
    def build_edges(self, extracted_app_data: dict) -> list[dict]:
        """
        Convert extracted Django data into a list of edge dicts.
        Must return:
            [
                {
                    "id": "...",
                    "type": "...",
                    "source": "...",
                    "target": "...",
                    "label": "...",
                    "metadata": {...}
                },
                ...
            ]
        """
        pass
