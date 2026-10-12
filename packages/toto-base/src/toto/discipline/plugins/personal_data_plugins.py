"""Discipline in "Download my data": the mana balance, every change to it,
the sigils held and the history of holding them."""

from toto.core.personal_data import PersonalDataPlugin, Table


@PersonalDataPlugin.plugin(key="discipline")
class DisciplineData(PersonalDataPlugin):
    def tables(self, user) -> list[Table]:
        from toto.discipline.models import ManaAccount, ManaEvent, Sigil, SigilEvent

        account = ManaAccount.objects.filter(user=user).first()
        balance = [{"mana": str(account.balance), "changed": account.updated_at.isoformat()}] \
            if account is not None else []
        events = [{"when": event.created_at.isoformat(), "change": str(event.delta),
                   "balance_after": str(event.balance_after), "kind": event.kind,
                   "for": event.metric_code,
                   "quantity": "" if event.quantity is None else str(event.quantity),
                   "unit": event.unit, "reason": event.reason}
                  for event in ManaEvent.objects.filter(user=user).order_by("created_at", "pk")]
        held = [{"sigil": str(sigil.uuid), "name": sigil.name,
                 "mana_an_hour": str(sigil.regen_per_hour),
                 "since": sigil.assigned_at.isoformat() if sigil.assigned_at else ""}
                for sigil in Sigil.objects.filter(holder=user, active=True)]
        history = [{"when": event.at.isoformat(), "sigil": str(event.sigil.uuid),
                    "name": event.sigil.name, "what": event.action}
                   for event in SigilEvent.objects.filter(user=user).select_related("sigil")
                   .order_by("at", "pk")]
        return [
            Table("discipline_mana", "Your mana: the balance now.", balance),
            Table("discipline_mana_changes",
                  "Every change to your mana: the hourly regeneration, what an action "
                  "spent and what it was for, what an administrator granted and the "
                  "reason they gave.", events),
            Table("discipline_sigils", "The sigils you hold now.", held),
            Table("discipline_sigil_history",
                  "When a sigil was assigned to you and when it was revoked.", history),
        ]
