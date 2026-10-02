"""A member's text never becomes code in the browser (2026-10-02, crown 41).

Two ways Django's escaping did not keep it out:

* ``escapejs`` inside a JavaScript TEMPLATE literal — ``JSON.parse(`{{ x|escapejs
  }}`)``. The filter is for a quoted string; it leaves ``${…}`` alone, and a
  template literal runs it. The Locations map, a route's, a zone's and a
  location's page, the route search and the ACE editor all did it, so a route
  named ``${alert(document.domain)}`` or a file holding ``${HOME}`` ran (or
  broke the page). They hand their data over as ``json_script`` blocks now,
  and this walks every installed app's templates so the shape cannot return.
* ``{{ … }}`` inside a quoted string in an Alpine directive (``x-show``,
  ``@click`` …): Django turns ``'`` into ``&#x27;`` and the browser turns it
  back before Alpine evaluates the attribute, so a name like ``x'+alert(1)+'``
  ran. The map widget did it with each place's name; it reads the name from a
  ``data-`` attribute now (the planning page's filter too:
  ``toto.events.tests_more_access.PlanPageMarkupTests``).

    manage.py test toto.core.tests_script_literals
"""

import re
from html.parser import HTMLParser
from pathlib import Path

from django.template.loader import render_to_string
from django.test import SimpleTestCase

#: ``escapejs`` output between backticks on one line.
IN_TEMPLATE_LITERAL = re.compile(r"`[^`\n]*\{\{[^}\n]*\|\s*escapejs[^}\n]*\}\}[^`\n]*`")


def alpine_directives(html):
    """``[(name, value)]`` of every Alpine directive on the page, the value as
    the browser hands it to Alpine (entities decoded)."""
    found = []

    class Collect(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.extend((name, value or "") for name, value in attrs
                         if name.startswith(("x-", "@", ":")))

    Collect().feed(html)
    return found


class ScriptLiteralTests(SimpleTestCase):
    def test_escapejs_never_sits_in_a_template_literal(self):
        """Every installed app's templates — the ones Django serves."""
        from django.apps import apps

        offenders = []
        for config in apps.get_app_configs():
            for template in (Path(config.path) / "templates").glob("**/*.html"):
                text = template.read_text(encoding="utf-8", errors="replace")
                for number, line in enumerate(text.splitlines(), 1):
                    if IN_TEMPLATE_LITERAL.search(line):
                        offenders.append(f"{template}:{number}")
        self.assertEqual(offenders, [])

    def test_the_map_widget_reads_a_places_name_as_data(self):
        name = "Pier'+alert(document.domain)+'"
        html = render_to_string("oya/partials/map.html", {"widget": {
            "id": "w1", "title": "Harbour", "center": "[54.35, 18.65]", "zoom": 9,
            "features": [{"name": name, "type": "Place", "geometry": None}]}})
        self.assertEqual([(attr, value) for attr, value in alpine_directives(html)
                          if "alert(document.domain)" in value], [])
        self.assertIn('data-name="Pier&#x27;+alert(document.domain)+&#x27;"', html)
