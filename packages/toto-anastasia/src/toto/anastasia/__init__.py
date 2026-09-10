"""Anastasia: reserved compute capacity, mounted Capsules, disposable runners.

Two halves, deliberately separable:

* the **Django app** (this package's models, services, checks) — installed on a
  host that wants to hand its users compute. It owns the booking arithmetic and
  the durable record of what a user reserved.
* the **manager** (:mod:`toto.anastasia.executor`) — a small trusted process that
  is the ONLY thing in the platform allowed to talk to Docker. It must import
  with no Django configured at all; a test asserts that.

Nothing here knows what a document, a dataset or a workflow is. See
``portal/anastasia.md`` for the architecture and the ownership boundaries.
"""
