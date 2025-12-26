from toto.colors import ColorGenerator


class GraphStyleResolver:
    FALLBACK_NODE_COLOR = "#888888"
    FALLBACK_EDGE_COLOR = "#888888"
    FALLBACK_NODE_SIZE = 20
    FALLBACK_EDGE_SIZE = 2

    def __init__(self, cypher_query, palette="tab20"):
        self.generator = ColorGenerator(palette)

        self.node_styles = {
            ns.collection_type.name: ns
            for ns in cypher_query.node_styles.all()
        }

        self.edge_styles = {
            es.relation_type.name: es
            for es in cypher_query.edge_styles.all()
        }

    def node_color(self, label):
        style = self.node_styles.get(label)
        return style.color if style else self.FALLBACK_NODE_COLOR

    def node_size(self, label):
        style = self.node_styles.get(label)
        return style.size if style else self.FALLBACK_NODE_SIZE

    def edge_color(self, rel_type):
        style = self.edge_styles.get(rel_type)
        return style.color if style else self.FALLBACK_EDGE_COLOR

    def edge_size(self, rel_type):
        style = self.edge_styles.get(rel_type)
        return style.size if style else self.FALLBACK_EDGE_SIZE
