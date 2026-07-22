from django.db.models import Q
from django.utils.dateparse import parse_datetime

from toto.core.connectors import (
    ConnectorExecutionError,
    ReadOnlyModelConnector,
    filter_search,
    get_object_by_config,
    iso,
    minimal_person,
    register_connector,
)
from toto.locations.connectors import serialize_address


@register_connector
class EventsReadConnector(ReadOnlyModelConnector):
    connector_type = "events_read"
    label = "Events read"
    app_label = "events"
    allowed_resources = {"scheduled_event", "category", "invite", "availability"}

    def validate(self) -> list[str]:
        errors = super().validate()
        resource = self.config.get("resource", "scheduled_event")
        if resource not in self.allowed_resources:
            errors.append(
                f"Connector resource must be one of: {', '.join(sorted(self.allowed_resources))}."
            )
        return errors

    def execute(self, input_data: dict | None = None) -> dict:
        input_data = input_data or {}
        resource = self.config.get("resource", "scheduled_event")

        if resource == "scheduled_event":
            return self._execute_scheduled_event(input_data)
        if resource == "category":
            return self._execute_category(input_data)
        if resource == "invite":
            return self._execute_invite(input_data)
        if resource == "availability":
            return self._execute_availability(input_data)

        raise ConnectorExecutionError(f"Unsupported events resource: {resource}")

    def _execute_scheduled_event(self, input_data: dict) -> dict:
        from toto.events.models import ScheduledEvent

        qs = ScheduledEvent.objects.select_related("category", "owner", "address").prefetch_related("organizers")
        qs = self._filter_event_window(qs, input_data)
        qs = self._filter_public(qs)

        owner_slug = self.configured_value("owner_slug", "owner_slug_field", input_data, default=None)
        if owner_slug:
            qs = qs.filter(owner__slug=owner_slug)

        organizer_slug = self.configured_value("organizer_slug", "organizer_slug_field", input_data, default=None)
        if organizer_slug:
            qs = qs.filter(organizers__slug=organizer_slug)

        category = self.configured_value("category", "category_field", input_data, default=None)
        if category:
            qs = qs.filter(category__name=category)

        if self.config.get("action", "list") == "get":
            event = get_object_by_config(qs, self, input_data, default_lookup="id")
            return {"data": {"event": serialize_scheduled_event(event)}}

        query = self.query_value(input_data)
        qs = filter_search(
            qs,
            self,
            input_data,
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(category__name__icontains=query),
        )
        return {"data": {"events": [serialize_scheduled_event(item) for item in qs.distinct()[:self.limit()]]}}

    def _execute_category(self, input_data: dict) -> dict:
        from toto.events.models import EventCategory

        qs = EventCategory.objects.all()
        if self.config.get("action", "list") == "get":
            category = get_object_by_config(qs, self, input_data, default_lookup="name")
            return {"data": {"category": serialize_event_category(category)}}
        query = self.query_value(input_data)
        qs = filter_search(qs, self, input_data, Q(name__icontains=query) | Q(description__icontains=query))
        return {"data": {"categories": [serialize_event_category(item) for item in qs.order_by("name")[:self.limit()]]}}

    def _execute_invite(self, input_data: dict) -> dict:
        from toto.events.models import EventInvite

        qs = EventInvite.objects.select_related("event", "event__category", "event__address", "person")
        person_slug = self.configured_value("person_slug", "person_slug_field", input_data, default=None)
        if person_slug:
            qs = qs.filter(person__slug=person_slug)
        status = self.configured_value("status", "status_field", input_data, default=None)
        if status:
            qs = qs.filter(status=status)
        event_id = self.configured_value("event_id", "event_id_field", input_data, default=None)
        if event_id:
            qs = qs.filter(event_id=event_id)

        if self.config.get("action", "list") == "get":
            invite = get_object_by_config(qs, self, input_data, default_lookup="id")
            return {"data": {"invite": serialize_invite(invite)}}

        query = self.query_value(input_data)
        qs = filter_search(
            qs,
            self,
            input_data,
            Q(event__title__icontains=query) | Q(person__display_name__icontains=query) | Q(note__icontains=query),
        )
        return {"data": {"invites": [serialize_invite(item) for item in qs[:self.limit()]]}}

    def _execute_availability(self, input_data: dict) -> dict:
        from toto.events.models import Availability

        qs = Availability.objects.select_related("person")
        person_slug = self.configured_value("person_slug", "person_slug_field", input_data, default=None)
        if person_slug:
            qs = qs.filter(person__slug=person_slug)
        availability_type = self.configured_value("availability_type", "availability_type_field", input_data, default=None)
        if availability_type:
            qs = qs.filter(availability_type=availability_type)
        qs = self._filter_event_window(qs, input_data)

        if self.config.get("action", "list") == "get":
            availability = get_object_by_config(qs, self, input_data, default_lookup="id")
            return {"data": {"availability": serialize_availability(availability)}}

        query = self.query_value(input_data)
        qs = filter_search(
            qs,
            self,
            input_data,
            Q(person__display_name__icontains=query) | Q(reason__icontains=query),
        )
        return {"data": {"availabilities": [serialize_availability(item) for item in qs[:self.limit()]]}}

    def _filter_public(self, qs):
        public_only = self.config.get("public_only")
        if public_only is None:
            public_only = True
        if public_only:
            return qs.filter(public=True)
        return qs

    def _filter_event_window(self, qs, input_data: dict):
        starts_after = self.configured_value("starts_after", "starts_after_field", input_data, default=None)
        if starts_after:
            qs = qs.filter(start_time__gte=_parse_datetime(starts_after, "starts_after"))
        starts_before = self.configured_value("starts_before", "starts_before_field", input_data, default=None)
        if starts_before:
            qs = qs.filter(start_time__lte=_parse_datetime(starts_before, "starts_before"))
        ends_after = self.configured_value("ends_after", "ends_after_field", input_data, default=None)
        if ends_after:
            qs = qs.filter(end_time__gte=_parse_datetime(ends_after, "ends_after"))
        ends_before = self.configured_value("ends_before", "ends_before_field", input_data, default=None)
        if ends_before:
            qs = qs.filter(end_time__lte=_parse_datetime(ends_before, "ends_before"))
        return qs


def serialize_event_category(category) -> dict:
    return {
        "id": category.id,
        "uid": str(category.uid),
        "name": category.name,
        "description": category.description,
    }


def serialize_scheduled_event(event) -> dict:
    return {
        "id": str(event.id),
        "uid": str(event.uid),
        "title": event.title,
        "description": event.description,
        "start_time": iso(event.start_time),
        "end_time": iso(event.end_time),
        "category": serialize_event_category(event.category) if event.category else None,
        "public": event.public,
        "owner": minimal_person(event.owner) if event.owner else None,
        "organizers": [minimal_person(person) for person in event.organizers.all()],
        "address": serialize_address(event.address) if event.address else None,
        "capacity": event.capacity,
        "requires_registration": event.requires_registration,
    }


def serialize_invite(invite) -> dict:
    return {
        "id": str(invite.id),
        "uid": str(invite.uid),
        "event": {
            "id": str(invite.event.id),
            "uid": str(invite.event.uid),
            "title": invite.event.title,
            "start_time": iso(invite.event.start_time),
            "end_time": iso(invite.event.end_time),
        },
        "person": minimal_person(invite.person),
        "status": invite.status,
        "note": invite.note,
        "sent_at": iso(invite.sent_at),
        "responded_at": iso(invite.responded_at),
    }


def serialize_availability(availability) -> dict:
    return {
        "id": str(availability.id),
        "uid": str(availability.uid),
        "person": minimal_person(availability.person),
        "start_time": iso(availability.start_time),
        "end_time": iso(availability.end_time),
        "availability_type": availability.availability_type,
        "reason": availability.reason,
        "blocks_scheduling": availability.blocks_scheduling,
    }


def _parse_datetime(value, field_name: str):
    parsed = parse_datetime(str(value))
    if parsed is None:
        raise ConnectorExecutionError(f"{field_name} must be an ISO datetime.")
    return parsed
