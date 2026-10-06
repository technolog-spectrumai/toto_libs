"""Percentages of the recorded holdings (stage 65), the empty register
included: nothing is ever divided by zero, and the page says what the
percentages are of.

    manage.py test toto.companies.tests_percentages
"""

from decimal import Decimal

from toto.companies.models import ShareHolding
from toto.companies.register import holdings_of, percentage, register_of
from toto.companies.testing import CompaniesTestCase, client_of, page_url

D = Decimal


class PercentageTests(CompaniesTestCase):
    def test_a_part_of_the_total(self):
        for quantity, total, expected in ((50, 100, "50.00"), (1, 3, "33.33"),
                                          (2, 3, "66.67"), (1, 8, "12.50"),
                                          (5, 5, "100.00"), (0, 5, "0.00"),
                                          (1, 1_000_000, "0.00"), (999, 1000, "99.90")):
            with self.subTest(quantity=quantity, total=total):
                self.assertEqual(percentage(quantity, total), D(expected))

    def test_two_places_rounded_half_up(self):
        self.assertEqual(percentage(1, 800), D("0.13"))      # 0.125
        self.assertEqual(percentage(1, 32), D("3.13"))       # 3.125
        self.assertEqual(percentage(3, 800), D("0.38"))      # 0.375
        self.assertEqual(str(percentage(1, 4)), "25.00")

    def test_nothing_recorded_is_no_percentage_and_no_division(self):
        for total in (0, None, -1):
            with self.subTest(total=total):
                self.assertIsNone(percentage(0, total))
                self.assertIsNone(percentage(7, total))

    def test_very_large_quantities_are_exact(self):
        big = 10 ** 18 - 1
        self.assertEqual(percentage(big, big * 3), D("33.33"))
        self.assertEqual(percentage(big, big), D("100.00"))
        self.assertEqual(percentage(1, big), D("0.00"))


class RegisterTests(CompaniesTestCase):
    def test_an_empty_register(self):
        register = register_of(self.acme)
        self.assertEqual(register.rows, [])
        self.assertEqual(register.total, 0)
        self.assertEqual(register.holders, 0)
        self.assertTrue(register.empty)

    def test_only_zeros_is_a_total_of_nothing_and_no_percentage(self):
        self.hold_shares(self.acme, self.hold, 0)
        self.hold_shares(self.acme, self.mia, 0)
        register = register_of(self.acme)
        self.assertEqual(register.total, 0)
        self.assertTrue(register.empty)
        self.assertEqual(register.holders, 2)
        self.assertEqual([row.percentage for row in register.rows], [None, None])

    def test_rows_total_and_percentages(self):
        self.hold_shares(self.acme, self.hold, 30)
        self.hold_shares(self.acme, self.mia, 50)
        self.hold_shares(self.acme, self.stan, 20)
        register = register_of(self.acme)
        self.assertEqual(register.total, 100)
        self.assertFalse(register.empty)
        self.assertEqual([(row.holding.person.slug, row.quantity, row.percentage)
                          for row in register.rows],
                         [("mia", 50, D("50.00")), ("hold", 30, D("30.00")),
                          ("stan", 20, D("20.00"))])

    def test_a_zero_holding_beside_others_is_zero_percent(self):
        self.hold_shares(self.acme, self.hold, 0)
        self.hold_shares(self.acme, self.mia, 8)
        by_slug = {row.holding.person.slug: row.percentage
                   for row in register_of(self.acme).rows}
        self.assertEqual(by_slug, {"mia": D("100.00"), "hold": D("0.00")})

    def test_thirds_are_rounded_each_and_need_not_add_to_a_hundred(self):
        for person in (self.hold, self.mia, self.stan):
            self.hold_shares(self.acme, person, 1)
        parts = [row.percentage for row in register_of(self.acme).rows]
        self.assertEqual(parts, [D("33.33")] * 3)
        self.assertEqual(sum(parts), D("99.99"))

    def test_the_total_is_this_companys_alone(self):
        self.hold_shares(self.acme, self.hold, 10)
        self.hold_shares(self.globex, self.hold, 990)
        self.assertEqual(register_of(self.acme).total, 10)
        self.assertEqual(register_of(self.acme).rows[0].percentage, D("100.00"))
        self.assertEqual(register_of(self.globex).total, 990)

    def test_a_persons_percentage_is_of_each_companys_own_total(self):
        self.hold_shares(self.acme, self.hold, 10)
        self.hold_shares(self.acme, self.mia, 30)
        self.hold_shares(self.globex, self.hold, 1)
        self.hold_shares(self.globex, self.stan, 0)
        rows = {row.holding.community.slug: row.percentage
                for row in holdings_of(self.hold, self.mia_user)}
        self.assertEqual(rows, {"acme": D("25.00"), "globex": D("100.00")})

    def test_a_persons_holding_in_a_register_of_zeros_has_no_percentage(self):
        self.hold_shares(self.acme, self.hold, 0)
        rows = holdings_of(self.hold, self.mia_user)
        self.assertEqual([(row.quantity, row.percentage) for row in rows], [(0, None)])


class PageTests(CompaniesTestCase):
    def tab(self, user=None):
        return client_of(user or self.mia_user).get(page_url(self.acme, "shareholdings"))

    def test_the_empty_register_draws_with_a_total_of_zero(self):
        response = self.tab()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="company-register-empty"')
        self.assertContains(response, "No holdings are recorded yet.")
        self.assertContains(response, '<span data-testid="company-total">0</span>', html=True)
        self.assertNotContains(response, 'data-testid="company-register"')

    def test_a_register_of_zeros_draws_dashes(self):
        self.hold_shares(self.acme, self.hold, 0)
        response = self.tab()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="company-register"')
        self.assertContains(response, "—")
        self.assertNotContains(response, "0.00%")
        self.assertNotContains(response, "100%")

    def test_the_tab_shows_holder_quantity_percentage_and_total(self):
        self.hold_shares(self.acme, self.hold, 1)
        self.hold_shares(self.acme, self.mia, 2)
        response = self.tab()
        text = response.content.decode()
        for expected in ("Hold", "Mia", "33.33%", "66.67%", "Total recorded shares",
                         '<span data-testid="company-total">3</span>'):
            self.assertIn(expected, text)
        self.assertLess(text.index("66.67%"), text.index("33.33%"))   # the largest first

    def test_the_basis_of_the_percentages_is_said_in_words(self):
        response = self.tab()
        self.assertContains(response, 'data-testid="company-basis"')
        self.assertContains(response, "Percentages are of the shares recorded here")
        self.assertContains(response, "share capital")
        self.assertContains(response, "% of recorded shares" if ShareHolding.objects.exists()
                            else "Percentages are of the shares recorded here")

    def test_the_column_is_named_for_its_basis(self):
        self.hold_shares(self.acme, self.hold, 1)
        self.assertContains(self.tab(), "% of recorded shares")
