from toto.kanban.plugins.mission_plugins import MissionPlugin

from ..models import Contract, ContractNode


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


@MissionPlugin.plugin(
    key="economy",
    title="Economy",
    order=50,
)
class MissionEconomyPlugin(MissionPlugin):
    template_name = "contracts/kanban_plugins/mission_economy.html"
    section_icon = "fa-solid fa-coins"

    def _get_data(self, mission):
        from toto.kanban.models import Practitioner

        linked_nodes = ContractNode.objects.filter(
            object_app="kanban",
            object_model="mission",
            object_id=str(mission.pk),
        ).select_related("contract")
        linked_contracts = [n.contract for n in linked_nodes]

        practitioners = (
            Practitioner.objects
            .filter(assigned_tasks__mission=mission, is_active=True)
            .select_related("person")
            .distinct()
        )
        practitioner_rows = []
        for p in practitioners:
            payroll = list(_payroll_contracts_for_person(p.person))
            practitioner_rows.append({"practitioner": p, "payroll": payroll})

        return linked_contracts, practitioner_rows

    def is_visible_for_mission(self, **kwargs) -> bool:
        mission = kwargs["mission"]
        linked_contracts, practitioner_rows = self._get_data(mission)
        return bool(linked_contracts or practitioner_rows)

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        mission = kwargs["mission"]
        linked_contracts, practitioner_rows = self._get_data(mission)
        context["economy_mission"] = mission
        context["economy_linked_contracts"] = linked_contracts
        context["economy_practitioner_rows"] = practitioner_rows
        return context
