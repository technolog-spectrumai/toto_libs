from .transport_registry import get_transport_for_rule


class RemotePreviewClient:
    """
    Live remote preview.

    Uses the same backend as the rule direction.
    No cache. No import. No local persistence.
    """

    def __init__(self, rule):
        self.rule = rule

    def get_transport(self):
        return get_transport_for_rule(self.rule)

    def list_objects(self):
        return self.get_transport().list_remote_objects(
            model_label=self.rule.model_label,
        )

    def get_object(self, uid):
        return self.get_transport().get_remote_object(
            model_label=self.rule.model_label,
            uid=uid,
        )
