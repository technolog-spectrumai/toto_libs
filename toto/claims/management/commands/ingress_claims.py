from datetime import timedelta

from django.utils import timezone

from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed demo claims primitives for user testing."

    def process(self):
        if not self.full:
            return
        self.stdout.write("📋  Seeding demo claims primitives…")
        accounts, asset = self._seed_accounts_and_asset()
        self._seed_entitlements(accounts)
        self._seed_obligations(accounts, asset)
        self._seed_schedules()
        self._seed_conditions()
        self._seed_allocations(accounts, asset)
        self._seed_events()
        self.stdout.write(self.style.SUCCESS("✅  Claims ingress complete."))

    # ------------------------------------------------------------------ #
    # Shared resources                                                     #
    # ------------------------------------------------------------------ #

    def _seed_accounts_and_asset(self):
        from toto.assets.models import Asset, LedgerAccount, LedgerTransaction
        from toto.assets.services.assets import create_asset

        specs = [
            ("claims-alice",   "Claims Alice",   "user"),
            ("claims-bob",     "Claims Bob",     "user"),
            ("claims-carol",   "Claims Carol",   "user"),
            ("claims-reserve", "Claims Reserve", "reserve"),
            ("claims-system",  "Claims System",  "system"),
        ]
        accounts = {}
        for code, name, atype in specs:
            acc, created = LedgerAccount.objects.get_or_create(
                code=code,
                defaults={"name": name, "account_type": atype, "active": True},
            )
            accounts[code] = acc
            if created:
                self.stdout.write(f"  + account {code}")

        ref = "create-claims-cdemo"
        if LedgerTransaction.objects.filter(reference=ref).exists():
            asset = Asset.objects.get(unit_name="CDEMO")
        else:
            asset = create_asset(
                name="CDEMO",
                unit_name="CDEMO",
                total_supply=500_000,
                decimals=2,
                reserve_account=accounts["claims-reserve"],
                reference=ref,
            )
            self.stdout.write("  + asset CDEMO")

        return accounts, asset

    # ------------------------------------------------------------------ #
    # Entitlements                                                         #
    # ------------------------------------------------------------------ #

    def _seed_entitlements(self, accounts):
        from toto.claims.models import (
            Entitlement, EntitlementKind, EntitlementStatus,
        )
        alice = accounts["claims-alice"]
        bob   = accounts["claims-bob"]
        carol = accounts["claims-carol"]

        specs = [
            (alice, EntitlementKind.SERVICE_ACCESS,   EntitlementStatus.ACTIVE,    "Platform API Access", "claims_ingress", "demo-1"),
            (bob,   EntitlementKind.LEASE_RIGHT,       EntitlementStatus.ACTIVE,    "Office Suite Lease", "claims_ingress", "demo-2"),
            (carol, EntitlementKind.VESTING_RIGHT,     EntitlementStatus.SUSPENDED, "Founder Token Vest", "claims_ingress", "demo-3"),
            (alice, EntitlementKind.EXERCISE_RIGHT,    EntitlementStatus.EXPIRED,   "Option Exercise Window", "claims_ingress", "demo-4"),
            (bob,   EntitlementKind.REWARD_ELIGIBILITY,EntitlementStatus.ACTIVE,    "Q1 Bonus Eligibility", "claims_ingress", "demo-5"),
            (carol, EntitlementKind.CLAIM_RIGHT,       EntitlementStatus.REVOKED,   "Dividend Claim 2025", "claims_ingress", "demo-6"),
            (alice, EntitlementKind.USAGE_RIGHT,       EntitlementStatus.ACTIVE,    "Storage Quota 50GB", "claims_ingress", "demo-7"),
        ]
        for holder, kind, status, label, src_type, src_id in specs:
            obj, created = Entitlement.objects.get_or_create(
                source_type=src_type,
                source_id=src_id,
                defaults=dict(
                    holder_account=holder,
                    resource_label=label,
                    kind=kind,
                    status=status,
                    starts_at=timezone.now() - timedelta(days=30),
                    ends_at=timezone.now() + timedelta(days=180) if status != EntitlementStatus.EXPIRED else timezone.now() - timedelta(days=1),
                ),
            )
            if created:
                self.stdout.write(f"  + entitlement {label}")

    # ------------------------------------------------------------------ #
    # Obligations                                                          #
    # ------------------------------------------------------------------ #

    def _seed_obligations(self, accounts, asset):
        from toto.assets.models import Obligation, ObligationStatus
        alice  = accounts["claims-alice"]
        bob    = accounts["claims-bob"]
        carol  = accounts["claims-carol"]
        system = accounts["claims-system"]
        now = timezone.now()

        specs = [
            ("CLM-OBL-001", alice,  bob,    10_000, now + timedelta(days=30),  ObligationStatus.PENDING),
            ("CLM-OBL-002", bob,    carol,   5_000, now - timedelta(days=5),   ObligationStatus.PENDING),   # overdue
            ("CLM-OBL-003", carol,  system, 25_000, now - timedelta(days=1),   ObligationStatus.FULFILLED),
            ("CLM-OBL-004", alice,  system,  8_000, now + timedelta(days=90),  ObligationStatus.PENDING),
            ("CLM-OBL-005", bob,    alice,  15_000, now - timedelta(days=20),  ObligationStatus.DEFAULTED),
        ]
        for ref, debtor, creditor, amount, due_at, status in specs:
            obj, created = Obligation.objects.get_or_create(
                reference=ref,
                defaults=dict(
                    debtor_account=debtor,
                    creditor_account=creditor,
                    asset=asset,
                    amount_base_units=amount,
                    due_at=due_at,
                    status=status,
                    fulfilled_at=timezone.now() if status == ObligationStatus.FULFILLED else None,
                ),
            )
            if created:
                self.stdout.write(f"  + obligation {ref}")

    # ------------------------------------------------------------------ #
    # Schedules                                                            #
    # ------------------------------------------------------------------ #

    def _seed_schedules(self):
        from toto.claims.models import Schedule, ScheduleKind, ScheduleStatus
        now = timezone.now()

        specs = [
            ("Monthly Billing Cycle",     ScheduleKind.BILLING,    ScheduleStatus.ACTIVE,    "monthly",  "claims_ingress", "sched-1"),
            ("Annual Renewal Check",      ScheduleKind.RENEWAL,    ScheduleStatus.ACTIVE,    "yearly",   "claims_ingress", "sched-2"),
            ("Token Vesting Monthly",     ScheduleKind.VESTING,    ScheduleStatus.ACTIVE,    "monthly",  "claims_ingress", "sched-3"),
            ("Quarterly Payout",         ScheduleKind.PAYOUT,     ScheduleStatus.DRAFT,     "quarterly","claims_ingress", "sched-4"),
            ("Settlement T+2",            ScheduleKind.SETTLEMENT, ScheduleStatus.COMPLETED, "daily",    "claims_ingress", "sched-5"),
            ("Reward Distribution",       ScheduleKind.REWARD,     ScheduleStatus.PAUSED,    "monthly",  "claims_ingress", "sched-6"),
            ("Contract Expiry Check",     ScheduleKind.EXPIRY,     ScheduleStatus.ACTIVE,    "daily",    "claims_ingress", "sched-7"),
            ("Compliance Review",         ScheduleKind.REVIEW,     ScheduleStatus.CANCELLED, "yearly",   "claims_ingress", "sched-8"),
        ]
        for name, kind, status, freq, src_type, src_id in specs:
            obj, created = Schedule.objects.get_or_create(
                source_type=src_type,
                source_id=src_id,
                defaults=dict(
                    name=name,
                    kind=kind,
                    status=status,
                    frequency=freq,
                    starts_at=now - timedelta(days=60),
                    ends_at=now + timedelta(days=300) if status != ScheduleStatus.COMPLETED else now - timedelta(days=1),
                    next_run_at=now + timedelta(days=5) if status == ScheduleStatus.ACTIVE else None,
                    last_run_at=now - timedelta(days=25) if status != ScheduleStatus.DRAFT else None,
                ),
            )
            if created:
                self.stdout.write(f"  + schedule {name}")

    # ------------------------------------------------------------------ #
    # Conditions                                                           #
    # ------------------------------------------------------------------ #

    def _seed_conditions(self):
        from toto.claims.models import Condition, ConditionKind, ConditionStatus
        now = timezone.now()

        specs = [
            ("Within Active Lease Term",       ConditionKind.TIME,      ConditionStatus.PENDING,   "Lease end date not yet reached.",          {"type": "now_before", "datetime": (now + timedelta(days=180)).isoformat()},  "claims_ingress", "cond-1"),
            ("KYC Approval",                   ConditionKind.APPROVAL,  ConditionStatus.SATISFIED, "User KYC has been verified by compliance.", {},                                                                              "claims_ingress", "cond-2"),
            ("Minimum Balance ≥ 1000",         ConditionKind.BALANCE,   ConditionStatus.FAILED,    "Account must hold ≥ 1000 CDEMO.",           {"type": "balance_gte", "amount": 1000},                                        "claims_ingress", "cond-3"),
            ("Contract Status = Active",       ConditionKind.STATUS,    ConditionStatus.PENDING,   "Underlying contract must be active.",        {"type": "status_eq", "value": "active"},                                       "claims_ingress", "cond-4"),
            ("Evidence Submitted",             ConditionKind.EVIDENCE,  ConditionStatus.PENDING,   "Proof of delivery must be uploaded.",        {},                                                                              "claims_ingress", "cond-5"),
            ("Threshold: 3 of 5 Signatures",   ConditionKind.THRESHOLD, ConditionStatus.PENDING,   "Multi-sig threshold: 3 of 5 required.",      {"type": "multisig", "required": 3, "total": 5},                               "claims_ingress", "cond-6"),
            ("Manual Override Waived",         ConditionKind.MANUAL,    ConditionStatus.WAIVED,    "Admin manually waived this condition.",      {},                                                                              "claims_ingress", "cond-7"),
            ("External Oracle Confirms Price", ConditionKind.EXTERNAL,  ConditionStatus.CANCELLED, "Awaiting price feed from oracle.",           {"type": "oracle", "feed": "cdemo/usd"},                                        "claims_ingress", "cond-8"),
        ]
        for name, kind, status, desc, expr, src_type, src_id in specs:
            obj, created = Condition.objects.get_or_create(
                source_type=src_type,
                source_id=src_id,
                defaults=dict(
                    name=name,
                    kind=kind,
                    status=status,
                    description=desc,
                    expression=expr,
                    satisfied_at=now - timedelta(days=10) if status == ConditionStatus.SATISFIED else None,
                    failed_at=now - timedelta(days=2) if status == ConditionStatus.FAILED else None,
                ),
            )
            if created:
                self.stdout.write(f"  + condition {name}")

    # ------------------------------------------------------------------ #
    # Allocations                                                          #
    # ------------------------------------------------------------------ #

    def _seed_allocations(self, accounts, asset):
        from toto.claims.models import Allocation, AllocationKind, AllocationStatus
        alice  = accounts["claims-alice"]
        bob    = accounts["claims-bob"]
        system = accounts["claims-system"]
        now = timezone.now()

        specs = [
            (AllocationKind.RESERVE,       AllocationStatus.ACTIVE,    100_000, 0,      0,      alice,  system, "claims_ingress", "alloc-1"),
            (AllocationKind.ESCROW_HOLD,   AllocationStatus.ACTIVE,     25_000, 0,      0,      bob,    alice,  "claims_ingress", "alloc-2"),
            (AllocationKind.COLLATERAL,    AllocationStatus.RELEASED,   50_000, 0,      50_000, alice,  None,   "claims_ingress", "alloc-3"),
            (AllocationKind.VESTING_POOL,  AllocationStatus.ACTIVE,    200_000, 40_000, 0,      system, bob,    "claims_ingress", "alloc-4"),
            (AllocationKind.BUDGET,        AllocationStatus.CONSUMED,   10_000, 0,      10_000, alice,  bob,    "claims_ingress", "alloc-5"),
            (AllocationKind.PREPAID_BALANCE,AllocationStatus.DRAFT,      8_000, 0,      0,      bob,    None,   "claims_ingress", "alloc-6"),
            (AllocationKind.STAKING_LOCK,  AllocationStatus.ACTIVE,     75_000, 0,      0,      alice,  system, "claims_ingress", "alloc-7"),
            (AllocationKind.MARGIN,        AllocationStatus.EXPIRED,    15_000, 0,      0,      bob,    system, "claims_ingress", "alloc-8"),
        ]
        for kind, status, amount, allocated, released_or_consumed, holder, beneficiary, src_type, src_id in specs:
            consumed = released_or_consumed if status in (AllocationStatus.CONSUMED,) else 0
            released = released_or_consumed if status in (AllocationStatus.RELEASED,) else 0
            obj, created = Allocation.objects.get_or_create(
                source_type=src_type,
                source_id=src_id,
                defaults=dict(
                    asset=asset,
                    amount_base_units=amount,
                    allocated_amount_base_units=allocated,
                    released_amount_base_units=released,
                    consumed_amount_base_units=consumed,
                    kind=kind,
                    status=status,
                    holder_account=holder,
                    beneficiary_account=beneficiary,
                    starts_at=now - timedelta(days=30),
                    ends_at=now + timedelta(days=120) if status not in (AllocationStatus.EXPIRED, AllocationStatus.CONSUMED) else now - timedelta(days=1),
                ),
            )
            if created:
                self.stdout.write(f"  + allocation {kind} ({status})")

    # ------------------------------------------------------------------ #
    # ContractEvents                                                       #
    # ------------------------------------------------------------------ #

    def _seed_events(self):
        from toto.claims.models import ContractEvent, ContractEventKind
        now = timezone.now()

        specs = [
            (ContractEventKind.CREATED,             "Entitlement created: Platform API Access",        "claims_ingress", "evt-1"),
            (ContractEventKind.ACTIVATED,            "Lease activated: Office Suite",                   "claims_ingress", "evt-2"),
            (ContractEventKind.CONDITION_SATISFIED,  "KYC condition satisfied for Alice",               "claims_ingress", "evt-3"),
            (ContractEventKind.PAYMENT_DUE,          "Monthly billing payment due",                     "claims_ingress", "evt-4"),
            (ContractEventKind.ALLOCATION_RELEASED,  "Collateral released after settlement",            "claims_ingress", "evt-5"),
            (ContractEventKind.OBLIGATION_FULFILLED, "Obligation CLM-OBL-003 fulfilled",                "claims_ingress", "evt-6"),
            (ContractEventKind.OBLIGATION_CREATED,   "Obligation CLM-OBL-001 created",                  "claims_ingress", "evt-7"),
            (ContractEventKind.CANCELLED,            "Option exercise entitlement cancelled",           "claims_ingress", "evt-8"),
            (ContractEventKind.PAUSED,               "Vesting schedule paused",                         "claims_ingress", "evt-9"),
            (ContractEventKind.DEFAULT,              "Obligation CLM-OBL-005 defaulted",                "claims_ingress", "evt-10"),
        ]
        for kind, title, src_type, src_id in specs:
            obj, created = ContractEvent.objects.get_or_create(
                source_type=src_type,
                source_id=src_id,
                defaults=dict(
                    kind=kind,
                    title=title,
                    description=f"Demo event: {title}",
                    payload={"demo": True, "source": src_id},
                ),
            )
            if created:
                self.stdout.write(f"  + event {title}")
