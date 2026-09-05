"""Seed the Business Center: one company, its owners, its ledger.

Everything here is behind ``--full``. A Business Center with no company is a
perfectly good empty one — unlike a workflow row some dispatcher needs, none of
this is machinery, so seeding it on every deploy would be putting a fake company
into somebody's production database.

**It reuses the people socialhub already seeds.** ``ingress_socialhub --full``
creates "Farfarele Brokker Inc." as a Community with five ``fbrokker_*`` staff;
this makes the same organisation exist in the Business Center, with the same
five people, and points ``community_ref`` at the community. Seeding a second,
differently-named company beside it would teach a reader that the two halves of
the platform are unrelated, which is the opposite of true. Where socialhub has
not been seeded the users are created here instead, so either order works.

The cap table is built through ``services.ownership``, never by writing
``ShareHolding`` rows: that service is the only thing that also writes the
append-only ``OwnershipEvent`` history, and a seed that skipped it would produce
a company whose ownership has no provenance — exactly the thing the ledger
exists to make impossible.
"""

from __future__ import annotations

from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone

from toto.ingress import IngressCommand

#: The house demo company, and deliberately the same string socialhub uses.
COMPANY_NAME = "Farfarele Brokker Inc."

#: username, display name, job title, department. The first four match
#: socialhub's COMPANY_STAFF so the two seeds describe one organisation.
STAFF = [
    ("fbrokker_ceo", "Dorotea Farfarele", "Founder & CEO", "Board"),
    ("fbrokker_cfo", "Ignacy Brokker", "Chief Financial Officer", "Finance"),
    ("fbrokker_ops", "Mira Wilkanowicz", "Head of Operations", "Operations"),
    ("fbrokker_dev", "Piotr Lasocki", "Lead Engineer", "Engineering"),
    ("fbrokker_sec", "Halina Ostrowska", "Company Secretary", "Board"),
]

#: Two classes, because one class teaches nothing. The founder's carry two
#: votes per unit, which is what makes the voting app's weight arithmetic
#: visible in the demo rather than a no-op.
SHARE_CLASSES = [
    ("Founder shares", "founder", Decimal("2"), Decimal("100.00")),
    ("Ordinary shares", "ordinary", Decimal("1"), Decimal("100.00")),
]

#: (username, share class slug, units). Deliberately NOT round-number equal:
#: a cap table that divides evenly hides every rounding question the ownership
#: service exists to answer.
CAP_TABLE = [
    ("fbrokker_ceo", "founder", Decimal("600")),
    ("fbrokker_cfo", "ordinary", Decimal("250")),
    ("fbrokker_ops", "ordinary", Decimal("100")),
    ("fbrokker_dev", "ordinary", Decimal("50")),
]

#: name, parent, description. A tree rather than a flat list, because the org
#: chart is one of the things the Business Center is for and a single level
#: would render as a row of boxes with nothing to read.
DEPARTMENTS = [
    ("Board", None, "Governs. Everything the company decides formally lands here."),
    ("Finance", "Board", "Books, receipts, filings and the annual accounts."),
    ("Operations", "Board", "The desk: what the company does on any given day."),
    ("Engineering", "Board", "Builds and runs what the desk sells."),
]


class Command(IngressCommand):
    help = "Seed a demo company, its cap table, departments and ledger (--full)."

    def process(self):
        if not self.full:
            self.stdout.write(
                "company: nothing to seed without --full "
                "(a Business Center with no company is a valid empty one)")
            return

        from toto.company.models import Company

        company, created = Company.objects.get_or_create(
            name=COMPANY_NAME,
            defaults=self._company_defaults(),
        )
        verb = "created" if created else "already present"
        self.stdout.write(f"company: {COMPANY_NAME} — {verb}")

        self._link_to_community(company)
        people = self._people()
        departments = self._departments(company)
        self._memberships(company, people, departments)
        parties = self._parties(company, people)
        classes = self._share_classes(company)
        self._cap_table(company, parties, classes, people)
        self._ledger(company, people)
        self._files(company, people)

    # -- the company itself ------------------------------------------------

    def _company_defaults(self) -> dict:
        from toto.company.models import CompanyForm

        return {
            # A sp. z o.o. because that is what the share-capital and cap-table
            # machinery is shaped for; a P.S.A. demo would need a different
            # capital story and would teach the wrong default.
            "form": CompanyForm.PL_SP_ZOO,
            "legal_form_label": "spółka z ograniczoną odpowiedzialnością",
            "registry_no": "0000123456",
            "tax_no": "5252445090",
            "statistical_no": "146070970",
            "seat": "ul. Przykładowa 12\n00-001 Warszawa\nPolska",
            "share_capital": Decimal("100000.00"),
            "capital_currency": "PLN",
            "note": "Demo company seeded by ingress_company --full.",
        }

    def _link_to_community(self, company) -> None:
        """Point at socialhub's community of the same name, when there is one.

        A string reference rather than a ForeignKey, matching the field's own
        design: the Business Center may import socialhub, never the reverse.
        """
        from django.apps import apps as django_apps

        if company.community_ref or not django_apps.is_installed("toto.socialhub"):
            return
        from toto.socialhub.models import Community

        community = Community.objects.filter(name=COMPANY_NAME).first()
        if community is None:
            return
        company.community_ref = getattr(community, "slug", "") or str(community.pk)
        company.save(update_fields=["community_ref"])
        self.stdout.write(f"  linked to socialhub community {company.community_ref}")

    # -- people ------------------------------------------------------------

    def _people(self) -> dict:
        """The five staff, reusing socialhub's users where they already exist."""
        User = get_user_model()
        people = {}
        for username, display, _title, _dept in STAFF:
            first, _, last = display.partition(" ")
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"first_name": first, "last_name": last,
                          "email": f"{username}@example.com"},
            )
            if created:
                # Unusable password: these are demo identities, not accounts
                # anybody should be able to sign in as.
                user.set_unusable_password()
                user.save(update_fields=["password"])
            people[username] = user
        self.stdout.write(f"  staff: {len(people)} people")
        return people

    def _person_for(self, user):
        """The toto.people Person behind a user, created if missing.

        Person rows are made explicitly by `ingress_socialhub`, never by a
        signal on user creation. `ingress_all` runs socialhub first, so in a
        normal deploy these already exist and this finds them — but a run of
        `ingress_company` on its own would otherwise seed a company with an
        empty staff list and no memberships, which is a demo that shows the
        org chart working and nobody in it.
        """
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.people"):
            return None
        from toto.people.models import Person

        person, _ = Person.objects.get_or_create(user=user)
        return person

    # -- structure ---------------------------------------------------------

    def _departments(self, company) -> dict:
        from toto.company.models import Department

        made = {}
        for name, parent_name, description in DEPARTMENTS:
            parent = made.get(parent_name) if parent_name else None
            department, _ = Department.objects.get_or_create(
                company=company, name=name,
                defaults={"parent": parent, "description": description},
            )
            made[name] = department
        self.stdout.write(f"  departments: {len(made)}")
        return made

    def _memberships(self, company, people, departments) -> None:
        from toto.company.models import CompanyMembership

        count = 0
        for username, _display, title, dept_name in STAFF:
            person = self._person_for(people[username])
            if person is None:
                continue
            _, created = CompanyMembership.objects.get_or_create(
                company=company, person=person,
                defaults={"job_title": title,
                          "primary_department": departments.get(dept_name)},
            )
            count += int(created)
        self.stdout.write(f"  memberships: {count} new")

    # -- ownership ---------------------------------------------------------

    def _parties(self, company, people) -> dict:
        """A Party per shareholder.

        A Party is who may HOLD shares; a CompanyMembership is who WORKS here.
        They are separate on purpose — an investor holds without working, an
        employee works without holding — so the demo creates both rather than
        implying one from the other.
        """
        from toto.company.models import Party

        parties = {}
        for username, display, _title, _dept in STAFF:
            party, _ = Party.objects.get_or_create(
                company=company, name=display,
                defaults={"person": self._person_for(people[username])},
            )
            parties[username] = party
        return parties

    def _share_classes(self, company) -> dict:
        from toto.company.models import ShareClass

        classes = {}
        for name, slug, votes, nominal in SHARE_CLASSES:
            share_class, _ = ShareClass.objects.get_or_create(
                company=company, slug=slug,
                defaults={"name": name, "votes_per_unit": votes,
                          "nominal_value": nominal},
            )
            classes[slug] = share_class
        self.stdout.write(f"  share classes: {len(classes)}")
        return classes

    def _cap_table(self, company, parties, classes, people) -> None:
        """Issue through the service, so the history is real.

        `issue_shares` writes the OwnershipEvent as well as the holding. Writing
        ShareHolding rows directly would produce a cap table that is correct and
        unprovable, which for this app is worse than empty.
        """
        from toto.company.models import ShareHolding
        from toto.company.services.ownership import issue_shares

        recorded_by = people.get("fbrokker_sec")
        issued = 0
        for username, class_slug, units in CAP_TABLE:
            party = parties[username]
            share_class = classes[class_slug]
            # `until` is null while a holding is live — the shape
            # services.ownership._live_holding uses. Re-running the seed must
            # not issue the same shares twice.
            if ShareHolding.objects.filter(
                    party=party, share_class=share_class,
                    until__isnull=True).exists():
                continue
            issue_shares(company=company, target_party=party,
                         share_class=share_class, units=units,
                         recorded_by=recorded_by,
                         note="Founding issue (demo seed).")
            issued += 1
        total = sum(units for _u, _c, units in CAP_TABLE)
        self.stdout.write(f"  cap table: {issued} issues, {total} units total")

    # -- the ledger --------------------------------------------------------

    def _ledger(self, company, people) -> None:
        """Open the chain and record one real action on it.

        A ledger with nothing but a genesis block demonstrates nothing: the
        interesting property is that entry two hashes entry one, so the seed
        records an actual founding resolution and lets the chain prove itself.

        Skipped entirely where `toto.ledger` is not installed. A host can run
        the descriptive half of the Business Center alone, and seeding must not
        be the one thing that insists on the other half being there.
        """
        if not apps.is_installed("toto.ledger"):
            self.stdout.write("  ledger: toto.ledger not installed — skipped")
            return
        from toto.company.integration import ledger as bc_ledger
        from toto.company.models import ActionKind, CompanyAction

        actor = people.get("fbrokker_ceo")
        ledger = bc_ledger.company_ledger(company, actor=actor)
        self.stdout.write(f"  ledger: {ledger.key}")

        action, created = CompanyAction.objects.get_or_create(
            company=company,
            title="Founding resolution",
            defaults={
                "kind": ActionKind.RESOLUTION,
                "body": (
                    "The shareholders resolve to establish the company, adopt "
                    "its articles, and appoint the board as recorded in the "
                    "register of members."
                ),
            },
        )
        if created:
            bc_ledger.record_action(action, actor=actor)
            self.stdout.write("  ledger: founding resolution recorded")

        # Verify what was just written rather than trusting it. A seed that
        # produced a broken chain would otherwise be discovered by whoever
        # opened the page, not by the thing that wrote it.
        report = bc_ledger.verify_company_ledger(company)
        state = "intact" if report.ok else f"BROKEN at {report.first_bad_sequence}"
        self.stdout.write(
            f"  ledger: {report.checked} block(s), chain {state} "
            f"({timezone.localdate().isoformat()})")

    # -- the company's own files -------------------------------------------

    def _files(self, company, people) -> None:
        """A sheet, a document and a deck, in the company's own vault folder.

        One file per viewer this host ships — primula reads the sheet, htmlview
        the document, memo the deck — so each of those pages has something real
        to open on a fresh install instead of an empty list.

        They are the COMPANY's papers rather than three unrelated samples: a cap
        table that matches the share register seeded above, a profile that
        states the same registry number, and a deck that says the same thing to
        a room. A demo whose documents contradict its own database teaches
        people not to trust either.
        """
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.vault"):
            return
        owner = people.get("fbrokker_sec") or next(iter(people.values()), None)
        if owner is None:
            return

        bucket, directory = self._folder(company, owner)
        made = []
        made += self._sheet(company, bucket, directory, owner)
        made += self._document(company, bucket, directory, owner)
        made += self._deck(company, bucket, directory, owner)
        self.stdout.write(
            f"  files: {len(made)} new ({', '.join(made) or 'none'})"
            if made else "  files: already present")

    def _folder(self, company, owner):
        """The bucket and folder the company's papers live in.

        `Bucket.slug` is GLOBALLY unique, not unique per owner — so the slug
        carries the company, exactly as the vault's own `personal-<username>`
        convention does.
        """
        from toto.vault.models import Bucket, VaultDirectory

        bucket, _ = Bucket.objects.get_or_create(
            slug=f"company-{company.slug}",
            defaults={"name": company.name, "owner": owner,
                      "storage_backend": "local"},
        )
        directory, _ = VaultDirectory.objects.get_or_create(
            bucket=bucket, name="Company papers",
            defaults={"owner": owner},
        )
        return bucket, directory

    def _put(self, *, bucket, directory, owner, title, body, file_type):
        """One vault file, or nothing if it is already there."""
        from django.core.files.base import ContentFile
        from toto.vault.models import VaultFile

        if VaultFile.objects.filter(bucket=bucket, title=title).exists():
            return []
        vault_file = VaultFile(owner=owner, title=title, file_type=file_type,
                               bucket=bucket, directory=directory)
        vault_file.key = self._unique_key(bucket, title)
        vault_file.save()
        if isinstance(body, str):
            body = body.encode("utf-8")
        vault_file.file.save(title, ContentFile(body), save=True)
        return [title]

    @staticmethod
    def _unique_key(bucket, title):
        from django.utils.text import slugify
        from toto.vault.models import VaultFile

        base = slugify(title.rsplit(".", 1)[0]) or "file"
        candidate, suffix = base, 2
        while VaultFile.objects.filter(bucket=bucket, key=candidate).exists():
            candidate, suffix = f"{base}-{suffix}", suffix + 1
        return candidate

    def _sheet(self, company, bucket, directory, owner):
        """The cap table, as a real Univer workbook primula can open.

        Built from the SAME holdings the ownership service just wrote, so the
        sheet and the share register cannot disagree.

        The import is soft ON PURPOSE, and by importability rather than
        `is_installed`: `sheet_format` is a format library, not the viewer —
        zenobia seeds the sheet even in builds where the primula APP is off,
        because the module still resolves from its tree. A host that ships no
        primula at all simply gets no sheet, and no error.
        """
        try:
            from toto.primula import sheet_format
        except ImportError:
            return []
        from toto.company.models import ShareHolding

        rows = [["Shareholder", "Class", "Units", "Votes per unit"]]
        for holding in (ShareHolding.objects
                        .filter(share_class__company=company, until__isnull=True)
                        .select_related("party", "share_class")
                        .order_by("-units")):
            rows.append([holding.party.name, holding.share_class.name,
                         float(holding.units),
                         float(holding.share_class.votes_per_unit)])
        workbook = sheet_format.workbook_from_rows("Cap table", rows)
        return self._put(bucket=bucket, directory=directory, owner=owner,
                         title="cap-table.json",
                         body=sheet_format.dumps(workbook),
                         file_type="sheet")

    def _document(self, company, bucket, directory, owner):
        """A self-contained HTML profile, for the read-only document viewer.

        Self-contained on purpose: htmlview serves documents into a sandboxed
        iframe under a CSP of `default-src 'none'; img-src data:`, so anything
        fetching a stylesheet or a web font would render unstyled. This is what
        a document that survives that policy looks like.
        """
        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{company.name} — company profile</title>
<style>
  body {{ font-family: Georgia, 'Times New Roman', serif; margin: 2.5rem auto;
         max-width: 46rem; line-height: 1.6; color: #222; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 0.2rem; }}
  .sub {{ color: #666; font-style: italic; margin-top: 0; }}
  table {{ border-collapse: collapse; margin: 1.4rem 0; width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 0.45rem 0.6rem; text-align: left; }}
  th {{ background: #f5f5f5; width: 34%; }}
</style></head>
<body>
<h1>{company.name}</h1>
<p class="sub">{company.legal_form_label}</p>
<table>
  <tr><th>Registry number</th><td>{company.registry_no}</td></tr>
  <tr><th>Tax number</th><td>{company.tax_no}</td></tr>
  <tr><th>Statistical number</th><td>{company.statistical_no}</td></tr>
  <tr><th>Share capital</th><td>{company.share_capital} {company.capital_currency}</td></tr>
  <tr><th>Registered seat</th><td>{company.seat.replace(chr(10), '<br>')}</td></tr>
</table>
<p>This profile is seeded demo data. The figures above are the same ones held
in the company record and its share register — the cap table sheet beside this
file is generated from those holdings, and the founding resolution is recorded
on the company's own ledger.</p>
</body></html>"""
        return self._put(bucket=bucket, directory=directory, owner=owner,
                         title="company-profile.html", body=html,
                         file_type="html")

    def _deck(self, company, bucket, directory, owner):
        """A short deck for toto.memo, built through presentation_format.

        Through the format module rather than by writing XML by hand: it is the
        thing that decides what a valid deck is, and a seed that hand-rolled the
        markup would be the first file to break when the format moves.
        """
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.memo"):
            return []
        from toto.memo import presentation_format as fmt

        def slide(title, layout, blocks):
            return fmt.Slide(id=fmt._new_id("s"), title=title, layout=layout,
                             blocks=blocks)

        def text(payload, slot=""):
            return fmt.Block(id=fmt._new_id("b"), type="text",
                             payload=payload, slot=slot)

        def bullets(items, slot=""):
            return fmt.Block(id=fmt._new_id("b"), type="list",
                             items=items, slot=slot)

        deck = fmt.Presentation(
            title=f"{company.name} — company overview",
            slides=[
                slide(company.name, "section",
                      [text(company.legal_form_label)]),
                slide("Who we are", "title-content",
                      [bullets([
                          f"Registered as {company.registry_no}",
                          f"Share capital {company.share_capital} "
                          f"{company.capital_currency}",
                          "Four departments under one board",
                      ])]),
                slide("Ownership", "two-column",
                      [text("Two share classes. Founder shares carry two votes "
                            "per unit; ordinary shares carry one.", slot="left"),
                       bullets([f"{h.party.name} — {h.units:.0f} "
                                f"{h.share_class.name.lower()}"
                                for h in self._holdings(company)], slot="right")]),
                slide("Everything is on the ledger", "quote",
                      [text("Every company action is appended to a hash chain "
                            "that refuses to be edited — including the "
                            "resolution that founded this company.")]),
            ],
        )
        return self._put(bucket=bucket, directory=directory, owner=owner,
                         title="company-overview.pxml", body=fmt.dumps(deck),
                         file_type="pxml")

    @staticmethod
    def _holdings(company):
        from toto.company.models import ShareHolding

        return (ShareHolding.objects
                .filter(share_class__company=company, until__isnull=True)
                .select_related("party", "share_class")
                .order_by("-units"))
