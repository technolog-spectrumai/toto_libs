from django.contrib.auth.models import User

from toto.ingress import IngressCommand

from toto.contracts.models import Contract, ContractNode, ContractEdge
from toto.contracts.services import (
    create_node,
    create_manual_node,
    create_edge,
)


FOUNDER_STRONGBOX_PASSWORD = "qwerty"


class Command(IngressCommand):
    help = "Seed demo contract graph documents for user testing."

    def process(self):
        self.stdout.write("📜  Seeding demo contracts…")
        self._provision_founder_strongbox()
        self._seed_subscription_contract()
        self._seed_lease_contract()
        self._seed_escrow_contract()
        self._seed_vesting_contract()
        self._seed_reference_contract()
        self.stdout.write(self.style.SUCCESS("✅  Contracts ingress complete."))

    def _provision_founder_strongbox(self):
        """Ensure the Founder person has a gervazy strongbox (password: qwerty)."""
        from toto.gervazy.models import UserStrongbox
        from toto.gervazy.crypto import GervazyCryptoSession
        from toto.people.models import Person

        try:
            founder = Person.objects.get(display_name="Founder")
        except Person.DoesNotExist:
            self.stdout.write("  ! Founder person not found — skipping strongbox provisioning")
            return

        if not founder.user:
            self.stdout.write("  ! Founder has no linked user — skipping strongbox provisioning")
            return

        if UserStrongbox.objects.filter(owner=founder.user, name="founder-signing").exists():
            self.stdout.write("  ~ Founder signing strongbox already exists")
            return

        session, _ = GervazyCryptoSession.initialize_strongbox(
            founder.user,
            "founder-signing",
            FOUNDER_STRONGBOX_PASSWORD,
        )
        session.close()
        self.stdout.write(
            f"  + Founder signing strongbox created (password: {FOUNDER_STRONGBOX_PASSWORD!r})"
        )

    # ------------------------------------------------------------------ #
    # Subscription                                                         #
    # ------------------------------------------------------------------ #

    def _seed_subscription_contract(self):
        contract, created = Contract.objects.get_or_create(
            name="Subscription Claims Mesh",
            defaults={
                "description": (
                    "A subscription grants access to a service and creates a recurring "
                    "payment obligation on a billing schedule."
                ),
            },
        )
        if created:
            self.stdout.write("  + Subscription Claims Mesh")

        create_node(contract, "subscriber_access", "entitlement", "Subscriber Access",
                    description="Grants access to the service for the subscriber.")
        create_node(contract, "recurring_fee", "obligation", "Recurring Fee",
                    description="Monthly fee due from subscriber.")
        create_node(contract, "billing_cycle", "schedule", "Billing Cycle",
                    description="Monthly billing schedule.")
        create_node(contract, "active_subscription", "condition", "Active Subscription",
                    description="True while subscription is in good standing.")
        create_node(contract, "payment_event", "event", "Payment Event",
                    description="Emitted when a payment is processed.")
        create_manual_node(contract, "business_note", "Business Note", node_type="note",
                           description="Subscription renews automatically unless cancelled.")

        create_edge(contract, "billing_cycle", "recurring_fee", "triggers", label="triggers")
        create_edge(contract, "recurring_fee", "subscriber_access", "gates", label="gates")
        create_edge(contract, "active_subscription", "subscriber_access", "requires", label="requires")
        create_edge(contract, "payment_event", "recurring_fee", "settles", label="settles")
        create_edge(contract, "business_note", "billing_cycle", "explains", label="explains")


    # ------------------------------------------------------------------ #
    # Lease                                                                #
    # ------------------------------------------------------------------ #

    def _seed_lease_contract(self):
        contract, created = Contract.objects.get_or_create(
            name="Lease Claims Mesh",
            defaults={
                "description": (
                    "A time-based lease grants use rights, creates a rent obligation on a "
                    "schedule, and may hold a deposit in allocation."
                ),
            },
        )
        if created:
            self.stdout.write("  + Lease Claims Mesh")

        create_node(contract, "use_right", "entitlement", "Use Right",
                    description="Lease/use right held by the lessee.")
        create_node(contract, "rent_obligation", "obligation", "Rent Obligation",
                    description="Rent due per billing period.")
        create_node(contract, "rent_schedule", "schedule", "Rent Schedule",
                    description="Rent and expiry schedule.")
        create_node(contract, "within_lease_term", "condition", "Within Lease Term",
                    description="True while lease has not expired.")
        create_node(contract, "deposit_hold", "allocation", "Deposit Hold",
                    description="Security deposit reserved during lease term.")
        create_node(contract, "activated_event", "event", "Activated",
                    description="Emitted when the lease is activated.")
        create_node(contract, "expired_event", "event", "Expired",
                    description="Emitted when the lease expires.")
        create_manual_node(contract, "deposit_note", "Deposit Note", node_type="note",
                           description="Deposit is returned on clean exit, forfeited on default.")

        create_edge(contract, "rent_schedule", "rent_obligation", "triggers", label="triggers")
        create_edge(contract, "within_lease_term", "use_right", "gates", label="gates")
        create_edge(contract, "deposit_hold", "rent_obligation", "secures", label="secures")
        create_edge(contract, "activated_event", "use_right", "grants", label="grants")
        create_edge(contract, "expired_event", "use_right", "records", label="records end")
        create_edge(contract, "deposit_note", "deposit_hold", "explains", label="explains")


    # ------------------------------------------------------------------ #
    # Escrow (modelled with allocation + condition + obligation + event)   #
    # ------------------------------------------------------------------ #

    def _seed_escrow_contract(self):
        contract, created = Contract.objects.get_or_create(
            name="Escrow Claims Mesh",
            defaults={
                "description": (
                    "An escrow hold allocates funds, a release condition gates the release "
                    "obligation, and events record fund/release/refund."
                ),
            },
        )
        if created:
            self.stdout.write("  + Escrow Claims Mesh")

        create_node(contract, "escrow_hold", "allocation", "Escrow Hold",
                    description="Funds held in escrow pending release condition.")
        create_node(contract, "release_condition", "condition", "Release Condition",
                    description="Condition that must be satisfied for funds to be released.")
        create_node(contract, "release_duty", "obligation", "Release Duty",
                    description="Obligation to release funds once condition is met.")
        create_node(contract, "refund_duty", "obligation", "Refund Duty",
                    description="Obligation to refund funds if condition fails.")
        create_node(contract, "funded_event", "event", "Funded",
                    description="Emitted when escrow is funded.")
        create_node(contract, "released_event", "event", "Released",
                    description="Emitted when funds are released.")
        create_node(contract, "refunded_event", "event", "Refunded",
                    description="Emitted when funds are refunded.")
        create_manual_node(contract, "escrow_note", "Escrow Pattern", node_type="note",
                           description="Escrow = allocation + condition + obligation + event.")

        create_edge(contract, "release_condition", "release_duty", "gates", label="gates release")
        create_edge(contract, "release_condition", "refund_duty", "gates", label="gates refund")
        create_edge(contract, "escrow_hold", "release_duty", "allocates", label="allocates")
        create_edge(contract, "escrow_hold", "refund_duty", "allocates", label="allocates")
        create_edge(contract, "funded_event", "escrow_hold", "records", label="records")
        create_edge(contract, "released_event", "release_duty", "settles", label="settles")
        create_edge(contract, "refunded_event", "refund_duty", "settles", label="settles")
        create_edge(contract, "escrow_note", "escrow_hold", "explains", label="explains")


    # ------------------------------------------------------------------ #
    # Vesting (schedule + condition + allocation + entitlement + event)    #
    # ------------------------------------------------------------------ #

    def _seed_vesting_contract(self):
        contract, created = Contract.objects.get_or_create(
            name="Vesting Claims Mesh",
            defaults={
                "description": (
                    "A vesting schedule releases allocation tranches, subject to cliff "
                    "conditions, granting entitlement rights over time."
                ),
            },
        )
        if created:
            self.stdout.write("  + Vesting Claims Mesh")

        create_node(contract, "vesting_pool", "allocation", "Vesting Pool",
                    description="Total allocation available to vest over the schedule.")
        create_node(contract, "cliff_condition", "condition", "Cliff Condition",
                    description="Cliff period must be completed before vesting begins.")
        create_node(contract, "release_schedule", "schedule", "Release Schedule",
                    description="Schedule of tranche releases after the cliff.")
        create_node(contract, "vesting_right", "entitlement", "Vesting Right",
                    description="Beneficiary's right to vest tranches.")
        create_node(contract, "release_duty", "obligation", "Release Duty",
                    description="Obligation to release each tranche per schedule.")
        create_node(contract, "cliff_event", "event", "Cliff Reached",
                    description="Emitted when the cliff period is completed.")
        create_node(contract, "tranche_released", "event", "Tranche Released",
                    description="Emitted on each scheduled tranche release.")
        create_manual_node(contract, "vesting_note", "Vesting Pattern", node_type="note",
                           description="Vesting = schedule + condition + allocation + entitlement + obligation + event.")

        create_edge(contract, "cliff_condition", "release_schedule", "requires", label="requires")
        create_edge(contract, "release_schedule", "release_duty", "triggers", label="triggers")
        create_edge(contract, "vesting_pool", "release_duty", "allocates", label="allocates")
        create_edge(contract, "vesting_right", "vesting_pool", "held_by",
                    label="held by")
        create_edge(contract, "tranche_released", "release_duty", "settles", label="settles")
        create_edge(contract, "cliff_event", "cliff_condition", "records", label="records")
        create_edge(contract, "vesting_note", "vesting_pool", "explains", label="explains")


    # ------------------------------------------------------------------ #
    # Contract referencing another contract                                #
    # ------------------------------------------------------------------ #

    def _seed_reference_contract(self):
        # Ensure subscription exists
        try:
            sub = Contract.objects.get(name="Subscription Claims Mesh")
        except Contract.DoesNotExist:
            return

        contract, created = Contract.objects.get_or_create(
            name="SaaS Platform Bundle",
            defaults={
                "description": (
                    "A bundle contract that composes a subscription and a lease. "
                    "Demonstrates contract-to-contract references."
                ),
            },
        )
        if created:
            self.stdout.write("  + SaaS Platform Bundle")

        # Reference the subscription contract as a node
        create_node(contract, "sub_contract", "contract", "Subscription Contract",
                    object=sub,
                    description="References the Subscription Claims Mesh.")
        create_manual_node(contract, "platform_note", "Platform Note", node_type="note",
                           description="This bundle composes the subscription and lease contracts.")

        create_edge(contract, "platform_note", "sub_contract", "explains", label="explains")
        create_edge(contract, "sub_contract", "platform_note", "annotates", label="annotated by")

