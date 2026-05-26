from __future__ import annotations


def can_manage_robot_fleet(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_staff or user.has_perm("robots.change_robot")))


def can_dispatch_robot_mission(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_staff or user.has_perm("robots.dispatch_robot_mission")))


def can_issue_robot_command(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_staff or user.has_perm("robots.issue_robot_command")))
