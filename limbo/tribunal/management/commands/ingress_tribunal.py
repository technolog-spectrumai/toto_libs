from django.utils import timezone

from toto.ingress import IngressCommand
from toto.people.models import Person
from toto.tribunal.models import (
    TribunalCase,
    TribunalClaim,
    TribunalEvidence,
    TribunalParty,
)


class Command(IngressCommand):
    help = "Seed Tribunal demo disputes."

    def process(self):
        people = list(Person.objects.order_by("id")[:3])
        if len(people) < 2:
            self.stdout.write(self.style.WARNING("Tribunal ingress skipped: at least two people are required."))
            return

        opened_by = people[0]
        assigned_to = people[1]
        respondent = people[2] if len(people) > 2 else people[1]

        case, created = TribunalCase.objects.get_or_create(
            slug="custody-review-demo",
            defaults={
                "title": "Custody review for shared asset",
                "reason": "Conflicting custody records require review.",
                "description": (
                    "A demo tribunal case for reviewing custody, evidence, "
                    "claims, and final rulings inside the Oya interface."
                ),
                "opened_by": opened_by,
                "assigned_to": assigned_to,
                "status": "under_review",
                "priority": "high",
            },
        )
        if created:
            self.stdout.write(f"  + tribunal case {case.case_number or case.slug}")
        else:
            self.stdout.write(self.style.WARNING(f"  - tribunal case already exists: {case.case_number or case.slug}"))

        claimant_party, _ = TribunalParty.objects.get_or_create(
            case=case,
            person=opened_by,
            role="claimant",
            defaults={"statement": "Requests review of asset custody and supporting evidence."},
        )
        TribunalParty.objects.get_or_create(
            case=case,
            person=respondent,
            role="respondent",
            defaults={"statement": "Responds to the custody claim."},
        )

        TribunalClaim.objects.get_or_create(
            case=case,
            summary="Confirm rightful custodian",
            defaults={
                "claimant": claimant_party,
                "claim_type": "custody",
                "description": "Determine which party should hold custody of the object.",
                "requested_resolution": "Update custody records and close the dispute.",
            },
        )

        TribunalEvidence.objects.get_or_create(
            case=case,
            title="Initial custody statement",
            defaults={
                "submitted_by": claimant_party,
                "evidence_type": "statement",
                "description": f"Seed evidence recorded at {timezone.now():%Y-%m-%d %H:%M}.",
            },
        )

        self.stdout.write(self.style.SUCCESS("Tribunal ingress complete."))
