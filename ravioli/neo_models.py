from neomodel import StructuredNode, StructuredRel, StringProperty, JSONProperty, DateTimeProperty, RelationshipTo

class NeoRelation(StructuredRel):
    relation_type = StringProperty(required=True)
    label = StringProperty()
    metadata = JSONProperty()


class NeoDataNode(StructuredNode):
    name = StringProperty(required=True, unique_index=True)
    type = StringProperty(required=True)
    data = JSONProperty()

    # Here we pass the actual class, not a string
    relates_to = RelationshipTo('NeoDataNode', 'RELATES_TO', model=NeoRelation)
