"""Connector services.

Layering (each importable without the ones below it):

- ``mapping``    — pure spec validation + record value resolution (no I/O)
- ``extract``    — pluggable fetchers (REST v1) over ``toto.api.client``
- ``transform``  — records + mapping_spec → ingestor-schema proposal dict
- ``archive``    — raw payload → VaultFile audit trail
- ``runner``     — orchestrates a ConnectorRun end to end

Nothing here touches Neo4j directly: reads go through ``bento.graph_service``
(via the ingestor catalog/matching), writes only through
``toto.ingestor.services.apply``.
"""
