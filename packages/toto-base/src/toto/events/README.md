# toto.events

Scheduled events and personal availability calendar. Provides the event infrastructure used by mobilization for civilian emergency events.

## Purpose

Community organizers create `ScheduledEvent` records with a venue address, start/end time, and capacity. Members receive `EventInvite` records and RSVP. Personal `Availability` windows let members signal when they are free or busy for scheduling coordination. `EventBase` is also the abstract parent of `detections.Detection` — detections are time-anchored events with the same core fields.

## Models

- `EventCategory` — extends `DomainEntity`. Hierarchical category (self-referential parent).

- `EventBase` — abstract base (extends `DomainEntity`). Common fields: `category`, `title`, `description`, `starts_at`, `ends_at`, `is_public`, `is_cancelled`. Inherited by `ScheduledEvent` and also by `detections.Detection` (detections are time-anchored events).

- `ScheduledEvent` — a concrete event. Fields: `owner` (FK to `people.Person`), `organizers` (M2M to `people.Person`), `address` (FK to `locations.Address`), `max_participants` (nullable), `community` (FK to `socialhub.Community`), `registration_open`, `metadata`.

- `EventInvite` — an invitation from an event to a person. Fields: `event`, `person`, `status` (`pending / accepted / declined`), `sent_at`, `responded_at`.

- `Availability` — a person's availability window. Fields: `person` (FK to `people.Person`), `starts_at`, `ends_at`, `is_available` (bool; `False` = blocking), `recurrence` (JSON for repeating slots), `notes`.

## Key coupling

- `mobilization.MobilizationEvent.scheduled_event` — a mobilization event can be anchored to a calendar event.
- `EventBase` is the abstract parent of `detections.Detection`, so detections carry the same temporal fields.

## Dependencies

- `locations` — Event venue is an Address FK
- `people` — EventInvite invitee and Availability person
