"""The ownership ring of the Shareholdings tab (2026-10-06): what it is
handed, where it is drawn, and that a holder's name reaches it as data.

The owner: "when displaying shareholders view add pie chart to show the
structure of ownsership - use similat trick that file vault how mauch each
file types takes space".

    manage.py test toto.companies.tests_chart
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.contrib.staticfiles import finders

from toto.companies import register
from toto.companies.testing import CompaniesTestCase, client_of, member, page_url

RING = 'data-testid="company-ownership"'
DATA = re.compile(r'<script id="company-ownership-data" type="application/json">(.*?)</script>',
                  re.S)
_NODE = shutil.which("node")


def data_of(response):
    found = DATA.search(response.content.decode())
    return json.loads(found.group(1)) if found else None


class ChartDataTests(CompaniesTestCase):
    def chart(self, company=None):
        return register.chart_of(register.register_of(company or self.acme), "Other holders")

    def test_nothing_recorded_is_no_ring(self):
        self.assertIsNone(self.chart())
        self.hold_shares(self.acme, self.hold, 0)
        self.hold_shares(self.acme, self.mia, 0)
        self.assertIsNone(self.chart(), "only zeros: nothing to take a share of")

    def test_a_slice_per_holding_the_largest_first(self):
        self.hold_shares(self.acme, self.mia, 150)
        self.hold_shares(self.acme, self.hold, 500)
        self.hold_shares(self.acme, self.sen, 0)         # no slice for nothing
        self.hold_shares(self.globex, self.stan, 9)      # another company's
        chart = self.chart()
        self.assertEqual(chart["labels"], ["Hold", "Mia"])
        self.assertEqual(chart["values"], [500, 150])
        self.assertEqual(chart["shares"], ["500", "150"])
        self.assertEqual(chart["colours"], list(register.CHART_COLOURS[:2]))
        self.assertEqual(sum(chart["values"]), register.register_of(self.acme).total)

    def test_one_holder_is_the_whole_ring(self):
        self.hold_shares(self.acme, self.hold, 1000)
        self.assertEqual(self.chart()["values"], [1000])

    def test_a_long_register_ends_in_one_slice_for_the_rest(self):
        people = [member(f"h{at}", f"Holder {at:02d}")[1] for at in range(14)]
        for at, person in enumerate(people):
            self.hold_shares(self.acme, person, 100 - at)
        chart = self.chart()
        total = register.register_of(self.acme).total
        self.assertEqual(len(chart["labels"]), register.CHART_SLICES)
        self.assertEqual(chart["labels"][:2], ["Holder 00", "Holder 01"])
        self.assertEqual(chart["labels"][-1], "Other holders")
        self.assertEqual(chart["colours"][-1], register.CHART_REST_COLOUR)
        self.assertEqual(sum(chart["values"]), total, "the slices add to the total")
        self.assertEqual(chart["values"][-1], sum(100 - at for at in range(9, 14)))

    def test_exactly_the_cap_has_no_rest(self):
        for at in range(register.CHART_SLICES):
            self.hold_shares(self.acme, member(f"c{at}")[1], at + 1)
        chart = self.chart()
        self.assertEqual(len(chart["labels"]), register.CHART_SLICES)
        self.assertNotIn("Other holders", chart["labels"])

    def test_a_quantity_past_what_a_browser_counts_exactly_is_stated_as_text(self):
        self.hold_shares(self.acme, self.hold, 9007199254740993)
        self.assertEqual(self.chart()["shares"], ["9007199254740993"])


class ChartPageTests(CompaniesTestCase):
    def test_the_tab_draws_the_ring_for_every_signed_in_viewer(self):
        self.hold_shares(self.acme, self.hold, 500)
        self.hold_shares(self.acme, self.mia, 150)
        for user in (self.mia_user, self.stan_user, self.head_user, self.root_user):
            with self.subTest(user=user.username):
                response = client_of(user).get(page_url(self.acme, "shareholdings"))
                self.assertContains(response, RING)
                self.assertContains(response, "Ownership structure")
                self.assertContains(response, 'data-company-chart="company-ownership-data"')
                self.assertContains(response, "vendor/chartjs/chart.umd.min.js")
                self.assertContains(response, "companies/ownership_chart.js")
                self.assertEqual(data_of(response)["labels"], ["Hold", "Mia"])
                self.assertEqual(data_of(response)["values"], [500, 150])
                # The table is still there: the ring repeats it.
                self.assertContains(response, 'data-testid="company-register"')

    def test_an_empty_register_and_one_of_zeros_have_no_ring_and_load_no_script(self):
        for zeros in (False, True):
            if zeros:
                self.hold_shares(self.acme, self.hold, 0)
            with self.subTest(zeros=zeros):
                response = client_of(self.head_user).get(page_url(self.acme, "shareholdings"))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, RING)
                self.assertNotContains(response, "ownership_chart.js")
                self.assertNotContains(response, "chart.umd.min.js")
                self.assertIsNone(data_of(response))

    def test_the_overview_and_an_ordinary_community_have_none(self):
        self.hold_shares(self.acme, self.hold, 500)
        self.hold_shares(self.guild, self.hold, 5)
        for url in (page_url(self.acme), page_url(self.guild),
                    page_url(self.guild, "shareholdings")):
            with self.subTest(url=url):
                response = client_of(self.head_user).get(url)
                self.assertNotContains(response, RING)
                self.assertNotContains(response, "ownership_chart.js")

    def test_a_name_with_markup_reaches_the_ring_as_data(self):
        user, person = member("evil", '</script><img src=x onerror=alert(1)> "Q"')
        self.hold_shares(self.acme, person, 7)
        response = client_of(self.head_user).get(page_url(self.acme, "shareholdings"))
        text = response.content.decode()
        self.assertEqual(data_of(response)["labels"], ['</script><img src=x onerror=alert(1)> "Q"'])
        self.assertNotIn("<img src=x", text)
        self.assertNotIn("</script><img", text)


class ChartScriptTests(CompaniesTestCase):
    def source(self):
        return Path(finders.find("companies/ownership_chart.js")).read_text(encoding="utf-8")

    def test_it_is_a_doughnut_like_the_vault_s_and_writes_no_markup(self):
        source = self.source()
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith(("*", "/*", "//")))
        for expected in ('type: "doughnut"', 'cutout: "60%"', 'position: "bottom"'):
            self.assertIn(expected, code)
        for word in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                     "fetch(", "XMLHttpRequest", "sendBeacon", "setItem", "cookie"):
            self.assertNotIn(word, code, word)

    @skipUnless(_NODE, "node is not installed")
    def test_a_tip_states_the_shares_and_the_part_of_what_is_shown(self):
        body = r"""
          const G = require(process.argv[1]);
          const made = [];
          globalThis.Chart = function (canvas, config) { made.push(config); this.config = config; };
          globalThis.localStorage = {getItem: () => "true"};
          const data = {labels: ["Hold", "Mia", "Sen"], values: [500, 300, 200],
                        shares: ["500", "300", "200"], colours: ["#1", "#2", "#3"]};
          globalThis.document = {getElementById: (id) => id === "d" ? {textContent: JSON.stringify(data)} : null};
          const out = {};
          out.none = [G.draw({dataset: {companyChart: "missing"}}), made.length];
          G.draw({dataset: {companyChart: "d"}});
          const config = made[0];
          out.kind = [config.type, config.options.cutout, config.options.plugins.legend.position,
                      config.options.plugins.legend.labels.color, config.data.labels,
                      config.data.datasets[0].data, config.data.datasets[0].backgroundColor];
          const off = new Set();
          const chart = {data: config.data, getDataVisibility: (at) => !off.has(at)};
          const tip = (at) => config.options.plugins.tooltip.callbacks.label(
            {chart, datasetIndex: 0, dataIndex: at, label: data.labels[at]});
          out.all = [tip(0), tip(2)];
          off.add(0);
          out.without = [tip(1), tip(2)];
          out.percent = [G.percentOf(1, 3), G.percentOf(5, 0)];
          delete globalThis.Chart;
          out.noLibrary = G.draw({dataset: {companyChart: "d"}});
          console.log(JSON.stringify(out));
        """
        done = subprocess.run([_NODE, "-e", body, finders.find("companies/ownership_chart.js")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(out["none"], [None, 0], "no data, no ring")
        self.assertEqual(out["kind"], ["doughnut", "60%", "bottom", "#e2e8f0",
                                       ["Hold", "Mia", "Sen"], [500, 300, 200],
                                       ["#1", "#2", "#3"]])
        self.assertEqual(out["all"], ["Hold: 500 (50.00%)", "Sen: 200 (20.00%)"])
        self.assertEqual(out["without"], ["Mia: 300 (60.00%)", "Sen: 200 (40.00%)"],
                         "a holder switched off in the legend: shares of what is left")
        self.assertEqual(out["percent"], ["33.33", "0.00"])
        self.assertIsNone(out["noLibrary"], "without Chart.js nothing is drawn")
