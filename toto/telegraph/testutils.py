"""Test helpers for the data-mesh server gate.

Gated read endpoints (missions/tasks/locations/events/people) require the requester to be
an authenticated member of the `data_mesh` group. These helpers keep the existing API
tests green under that contract.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group


def mesh_group():
    group, _ = Group.objects.get_or_create(name="data_mesh")
    return group


def add_to_mesh(user):
    """Grant a user server access to gated data."""
    user.groups.add(mesh_group())
    return user


def login_mesh_member(testcase, username="mesh_member"):
    """Create a `data_mesh` member and log the test client in as them."""
    user = add_to_mesh(get_user_model().objects.create_user(username=username, password="pass"))
    testcase.client.force_login(user)
    return user
