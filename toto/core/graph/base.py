from neomodel import StructuredNode, StringProperty


class DomainNode(StructuredNode):
    __abstract_node__ = True
    uuid = StringProperty(
        unique_index=True,
        required=True
    )
