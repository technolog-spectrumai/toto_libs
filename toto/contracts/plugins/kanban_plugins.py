from toto.kanban.plugins.mission_plugins import MissionPlugin

from ..models import Contract


def _payroll_contracts_for_person(person):
    return Contract.objects.filter(
        metadata__archetype="payroll",
        nodes__key="worker_person",
        nodes__object_app="people",
        nodes__object_model="person",
        nodes__object_id=str(person.pk),
    ).distinct()


@MissionPlugin.plugin(
    key="payroll",
    title="Payroll",
    order=40,
)
class MissionPayrollPlugin(MissionPlugin):
    template_name = "contracts/kanban_plugins/mission_payroll.html"
    section_icon = "fa-solid fa-money-check-dollar"

    def _mission_rows(self, mission):
        from toto.kanban.models import Practitioner
        practitioners = (
            Practitioner.objects
            .filter(
                assigned_tasks__mission=mission,
                is_active=True,
            )
            .select_related("person")
            .distinct()
        )
        rows = []
        for p in practitioners:
            contracts = list(_payroll_contracts_for_person(p.person))
            rows.append({"practitioner": p, "contracts": contracts})
        return rows

    def is_visible_for_mission(self, **kwargs) -> bool:
        mission = kwargs["mission"]
        rows = self._mission_rows(mission)
        return any(r["contracts"] for r in rows)

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        context["payroll_rows"] = self._mission_rows(kwargs["mission"])
        return context
