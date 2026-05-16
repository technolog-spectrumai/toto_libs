from neomodel import (
    BooleanProperty,
    DateTimeProperty,
    IntegerProperty,
    JSONProperty,
    RelationshipTo,
    StringProperty,
)

from toto.core.graph.base import DomainNode
from toto.socialhub.graph.models import Person as PersonNode


class ProjectNode(DomainNode):
    __label__ = "KanbanProject"

    name = StringProperty()
    description = StringProperty()

    owner = RelationshipTo(PersonNode, "OWNED_BY")


class ColumnNode(DomainNode):
    __label__ = "KanbanColumn"

    name = StringProperty()
    position = IntegerProperty()
    can_add_task = BooleanProperty(default=False)

    project = RelationshipTo(ProjectNode, "BELONGS_TO_PROJECT")


class CampaignNode(DomainNode):
    __label__ = "KanbanCampaign"

    name = StringProperty()
    description = StringProperty()
    start_date = StringProperty()
    end_date = StringProperty()
    metadata = JSONProperty(default={})

    project = RelationshipTo(ProjectNode, "BELONGS_TO_PROJECT")
    owner = RelationshipTo(PersonNode, "OWNED_BY")


class MissionNode(DomainNode):
    __label__ = "KanbanMission"

    title = StringProperty()
    description = StringProperty()
    urgency = IntegerProperty()
    impact = IntegerProperty()
    metadata = JSONProperty(default={})

    campaign = RelationshipTo(CampaignNode, "BELONGS_TO_CAMPAIGN")
    owner = RelationshipTo(PersonNode, "OWNED_BY")


class SprintNode(DomainNode):
    __label__ = "KanbanSprint"

    name = StringProperty()
    start_time = DateTimeProperty()
    end_time = DateTimeProperty()

    project = RelationshipTo(ProjectNode, "BELONGS_TO_PROJECT")


class TaskNode(DomainNode):
    __label__ = "KanbanTask"

    title = StringProperty()
    description = StringProperty()
    due_date = StringProperty()
    position = IntegerProperty()
    weight = IntegerProperty()
    metadata = JSONProperty(default={})
    completed_at = DateTimeProperty()

    mission = RelationshipTo(MissionNode, "BELONGS_TO_MISSION")
    column = RelationshipTo(ColumnNode, "HAS_STATUS")
    sprint = RelationshipTo(SprintNode, "BELONGS_TO_SPRINT")
    assignee = RelationshipTo(PersonNode, "ASSIGNED_TO")
