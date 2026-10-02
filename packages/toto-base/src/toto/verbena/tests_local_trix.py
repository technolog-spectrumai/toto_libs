"""Trix from the image's own copy, never unpkg (2026-10-01, 37c.20).

django-trix-editor's widget hard-codes its two files at unpkg.com, so the
community news editor and the admin's news form told unpkg the address of
everybody who wrote a post, though the image carries both files
(download_vendor.py, `vendor/trix/`). `LocalTrixEditorWidget` keeps the
package's upload script and the admin's dark-theme styles and takes Trix
from /static/. A form cannot ask for it in `Meta.widgets`: the field's own
form field (`TrixEditorField.formfield`) puts the package's widget back
whatever it is given, so `use_local_trix` swaps it on the built form. The
admin guard walks every registered model with a Trix field for the same
reason.

Since 2026-10-02 the upload script is ours too (`verbena/trix_upload.js`):
the package's acted on a 200 alone, so a picture the door refused hung in
the editor at its progress bar with nothing said. ``UploadScriptTests``
runs whatever upload script the widget ships in node, against a page and
an XMLHttpRequest faked just far enough.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser
from unittest import skipUnless

from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles import finders
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import translation
from trix_editor.fields import TrixEditorField
from trix_editor.widgets import TrixEditorWidget

from toto.verbena.admin import make_section_form
from toto.verbena.widgets import UPLOAD_SCRIPT, LocalTrixEditorWidget, use_local_trix

#: Where the package fetches Trix from, and the copies the image carries.
UNPKG = "unpkg.com"
LOCAL_FILES = ("vendor/trix/trix.umd.min.js", "vendor/trix/trix.css")


def rendered(media) -> str:
    return str(media)


class WidgetTests(SimpleTestCase):
    def test_trix_comes_from_the_platforms_static_files(self):
        media = rendered(LocalTrixEditorWidget().media)
        self.assertNotIn(UNPKG, media)
        for path in LOCAL_FILES:
            self.assertIn(f"/static/{path}", media)

    def test_our_upload_script_replaces_the_packages_and_the_admins_styles_stay(self):
        media = rendered(LocalTrixEditorWidget().media)
        self.assertIn(f'src="/static/{UPLOAD_SCRIPT}"', media)
        self.assertIn('data-upload-url="/trix-editor/upload/"', media)
        self.assertNotIn("function uploadFile", media)  # the package's own script
        self.assertIn("trix-toolbar .trix-button-group", media)
        script = finders.find(UPLOAD_SCRIPT)
        self.assertIsNotNone(script, f"{UPLOAD_SCRIPT} is not a static file")
        with open(script, encoding="utf-8") as handle:
            self.assertIn('addEventListener("trix-attachment-add"', handle.read())

    def test_the_sentences_it_carries_are_in_the_readers_language(self):
        with translation.override("en"):
            media = rendered(LocalTrixEditorWidget().media)
        self.assertIn('data-upload-failed="Upload failed"', media)
        self.assertIn('data-upload-too-large="The picture is larger than 10 MB."', media)
        with translation.override("pl"):
            media = rendered(LocalTrixEditorWidget().media)
        self.assertNotIn("Upload failed", media)
        self.assertNotIn("The picture is larger", media)

    def test_a_form_with_two_editors_loads_it_once(self):
        """Two copies would send every picture twice."""
        class TwoEditors(forms.Form):
            first = forms.CharField(widget=LocalTrixEditorWidget)
            second = forms.CharField(widget=LocalTrixEditorWidget)

        self.assertEqual(rendered(TwoEditors().media).count(UPLOAD_SCRIPT), 1)

    def test_the_editor_element_is_the_packages(self):
        html = LocalTrixEditorWidget().render("content", "<p>Hi</p>", attrs={"id": "id_content"})
        self.assertIn('<trix-editor input="id_content"></trix-editor>', html)


class SwapTests(SimpleTestCase):
    def test_the_field_puts_the_packages_widget_back_whatever_it_is_given(self):
        """Why the swap is made on the built form: if the package ever honours
        the argument, `Meta.widgets` would do and this can go."""
        field = TrixEditorField().formfield(widget=LocalTrixEditorWidget)
        self.assertIs(type(field.widget), TrixEditorWidget)

    def test_a_trix_field_gets_the_local_widget_with_its_attributes(self):
        fields = {"content": TrixEditorField().formfield(),
                  "title": forms.CharField()}
        fields["content"].widget.attrs["data-x"] = "1"
        use_local_trix(fields)
        self.assertIsInstance(fields["content"].widget, LocalTrixEditorWidget)
        self.assertEqual(fields["content"].widget.attrs["data-x"], "1")
        self.assertIs(type(fields["title"].widget), forms.TextInput)

    def test_a_second_swap_changes_nothing(self):
        fields = {"content": TrixEditorField().formfield()}
        use_local_trix(fields)
        widget = fields["content"].widget
        use_local_trix(fields)
        self.assertIs(fields["content"].widget, widget)


class FormTests(SimpleTestCase):
    def test_the_community_news_editor_uses_the_local_copy(self):
        from toto.socialhub.forms import CommunityNewsPostForm

        form = CommunityNewsPostForm()
        self.assertIsInstance(form.fields["content"].widget, LocalTrixEditorWidget)
        self.assertNotIn(UNPKG, rendered(form.media))

    def test_a_section_form_uses_the_local_copy(self):
        from toto.socialhub.models import CommunityNewsPost

        form = make_section_form(CommunityNewsPost)()
        self.assertIsInstance(form.fields["content"].widget, LocalTrixEditorWidget)
        self.assertNotIn(UNPKG, rendered(form.media))


class AdminTests(TestCase):
    def test_no_admin_form_with_a_trix_field_loads_trix_from_unpkg(self):
        from django.contrib.auth import get_user_model

        request = RequestFactory().get("/admin/")
        request.user = get_user_model().objects.create_superuser("root", "r@example.org", "pw")
        checked = []
        for model, model_admin in admin.site._registry.items():
            if not any(isinstance(f, TrixEditorField) for f in model._meta.get_fields()):
                continue
            with self.subTest(model=model._meta.label):
                form = model_admin.get_form(request)()
                self.assertNotIn(UNPKG, rendered(form.media))
                checked.append(model._meta.label)
        self.assertIn("socialhub.CommunityNewsPost", checked, "vacuous: no Trix field found")


# ---------------------------------------------------------------------------
# The upload script's behaviour, in node (2026-10-02)
# ---------------------------------------------------------------------------

_NODE = shutil.which("node")

#: A page with editors and a fake XMLHttpRequest. Runs each script the
#: widget's media writes, in order, with document.currentScript standing for
#: its tag, then drops pictures into editors and answers their uploads as
#: the door, nginx or the network would. Prints what each editor shows.
_HARNESS = r"""
const vm = require("vm");
const scripts = JSON.parse(process.argv[1]);

class El {
  constructor(tag, attrs = {}) {
    Object.assign(this, {tagName: tag.toUpperCase(), attrs: Object.assign({}, attrs),
                         children: [], hidden: false, style: {cssText: ""}, after: null,
                         text: "", value: ""});
  }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  hasAttribute(name) { return name in this.attrs; }
  appendChild(child) { this.children.push(child); return child; }
  insertAdjacentElement(where, element) {
    if (where !== "afterend") throw new Error("unexpected position " + where);
    this.after = element;
    return element;
  }
  get textContent() { return this.children.map((c) => c.textContent).join("") || this.text; }
  set textContent(value) { this.children = []; this.text = String(value); }
  closest(selector) { return selector === "form" ? (this.form || null) : null; }
  querySelector(selector) {
    return selector === 'input[name="csrfmiddlewaretoken"]' ? (this.token || null) : null;
  }
}

// An exception in a listener is reported and the page goes on, as in a
// browser: each is kept here.
const thrown = [];
function call(fn, event) {
  try { fn(event); } catch (error) { thrown.push(String(error)); }
}

const requests = [];
class FakeXHR {
  constructor() {
    this.headers = {};
    this.listeners = {};
    const upload = {listeners: {}};
    upload.addEventListener = (type, fn) => (upload.listeners[type] ||= []).push(fn);
    this.upload = upload;
    requests.push(this);
  }
  open(method, url) { this.method = method; this.url = url; }
  setRequestHeader(name, value) { this.headers[name] = value; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  send(body) { this.body = body; }
  fire(type) { (this.listeners[type] || []).forEach((fn) => call(fn, {})); }
  respond(status, text) { this.status = status; this.responseText = text; this.fire("load"); }
  progress(loaded, total) {
    (this.upload.listeners.progress || []).forEach(
      (fn) => call(fn, {lengthComputable: true, loaded, total}));
  }
}
class FakeFormData {
  constructor() { this.fields = []; }
  append(name, value) { this.fields.push([name, value]); }
}

const listeners = {};
const page = {
  XMLHttpRequest: FakeXHR,
  FormData: FakeFormData,
  console,
  addEventListener: (type, fn) => (listeners[type] ||= []).push(fn),
  document: {cookie: "theme=dark; csrftoken=cookie-token", currentScript: null,
             createElement: (tag) => new El(tag)},
};
page.window = page;
vm.createContext(page);
function load() {
  for (const script of scripts) {
    page.document.currentScript = new El("script", script.attrs);
    vm.runInContext(script.code, page);
  }
  page.document.currentScript = null;
}
load();

function editor(token = "form-token") {
  const form = new El("form");
  if (token) form.token = Object.assign(new El("input"), {value: token});
  const element = new El("trix-editor");
  element.form = form;
  return element;
}
function picture(name = "photo.png") {
  return {file: {name, type: "image/png"}, removed: false, attributes: null, progress: [],
          remove() { this.removed = true; },
          setAttributes(attributes) { this.attributes = attributes; },
          setUploadProgress(value) { this.progress.push(value); }};
}
function drop(element, ...pictures) {
  const before = requests.length;
  for (const attachment of pictures) {
    (listeners["trix-attachment-add"] || []).forEach((fn) => call(fn, {target: element, attachment}));
  }
  return requests.slice(before);
}
function shown(element) {
  const note = element.after;
  if (!note || note.hidden) return null;
  return {role: note.getAttribute("role"), lines: note.children.map((c) => c.textContent)};
}
function view(element, attachment, sent) {
  const first = sent[0] || {};
  return {sent: sent.length, method: first.method || null, url: first.url || null,
          csrf: (first.headers || {})["X-CSRFToken"] || null,
          files: first.body ? first.body.fields.filter(([k]) => k === "file").map(([, v]) => v.name) : [],
          removed: attachment.removed, attributes: attachment.attributes,
          progress: attachment.progress, note: shown(element)};
}
function one(name, answer) {
  const element = editor(), attachment = picture(name), sent = drop(element, attachment);
  if (sent[0]) answer(sent[0]);
  return view(element, attachment, sent);
}

const out = {};
out.accepted = one("photo.png", (x) => {
  x.progress(50, 100);
  x.respond(200, JSON.stringify({attachment_url: "/media/trix_attachments/abc.png"}));
});
out.forbidden = one("photo.png", (x) => x.respond(403, JSON.stringify({error: "You may not attach files here."})));
out.notAPicture = one("page.svg", (x) => x.respond(400, JSON.stringify({error: "Only PNG, JPEG, GIF or WebP pictures can be attached."})));
out.tooLarge = one("big.jpg", (x) => x.respond(413, "<html><body><h1>413 Request Entity Too Large</h1></body></html>"));
out.serverError = one("photo.png", (x) => x.respond(502, "<html><body>502 Bad Gateway</body></html>"));
out.offline = one("photo.png", (x) => x.fire("error"));
out.lapsed = one("photo.png", (x) => x.respond(200, "<!doctype html><title>Sign in</title>"));

{
  const element = editor(), first = picture("one.png"), second = picture("two.svg");
  const sent = drop(element, first, second);
  sent[0].respond(400, JSON.stringify({error: "No."}));
  sent[1].respond(400, JSON.stringify({error: "Not either."}));
  out.batch = shown(element);
  const third = picture("three.png"), again = drop(element, third);
  out.cleared = shown(element);
  again[0].respond(200, JSON.stringify({attachment_url: "/media/trix_attachments/c.png"}));
  out.afterward = view(element, third, again);
}
{
  const element = editor(null), attachment = picture(), sent = drop(element, attachment);
  out.cookieCsrf = (sent[0] && sent[0].headers["X-CSRFToken"]) || null;
}
load();
{
  const element = editor(), sent = drop(element, picture());
  out.loadedTwiceSends = sent.length;
}
out.thrown = thrown;
console.log(JSON.stringify(out));
"""


class _Scripts(HTMLParser):
    """Each <script> in a piece of HTML, as its attributes and its code."""

    def __init__(self):
        super().__init__()
        self.found, self._open = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._open = {"attrs": {k: v or "" for k, v in attrs}, "code": ""}

    def handle_data(self, data):
        if self._open is not None:
            self._open["code"] += data

    def handle_endtag(self, tag):
        if tag == "script" and self._open is not None:
            self.found.append(self._open)
            self._open = None


def _shipped_scripts():
    """The scripts the widget's media loads, its static files read from
    disk; Trix itself is left out (only its events are needed, and they are
    faked)."""
    parser = _Scripts()
    parser.feed(rendered(LocalTrixEditorWidget().media))
    scripts = []
    for script in parser.found:
        src = script["attrs"].get("src")
        if src:
            if src.endswith("trix.umd.min.js"):
                continue
            path = finders.find(src[len(settings.STATIC_URL):])
            if path is None:
                raise AssertionError(f"{src} is not a static file")
            with open(path, encoding="utf-8") as handle:
                script["code"] = handle.read()
        scripts.append(script)
    return scripts


@skipUnless(_NODE, "node is not installed")
class UploadScriptTests(SimpleTestCase):
    """The editor's upload script as the widget ships it, in node."""

    _out = None

    def setUp(self):
        if UploadScriptTests._out is None:
            with translation.override("en"):
                scripts = _shipped_scripts()
            done = subprocess.run([_NODE, "-e", _HARNESS, json.dumps(scripts)],
                                  capture_output=True, text=True, timeout=60)
            if done.returncode != 0:
                raise AssertionError(f"node failed: {done.stderr}")
            UploadScriptTests._out = json.loads(done.stdout)
        self.out = UploadScriptTests._out

    def test_a_picture_the_door_takes_is_placed_in_the_editor(self):
        accepted = self.out["accepted"]
        self.assertEqual((accepted["sent"], accepted["method"], accepted["url"], accepted["files"]),
                         (1, "POST", "/trix-editor/upload/", ["photo.png"]))
        self.assertEqual(accepted["attributes"], {"url": "/media/trix_attachments/abc.png"})
        self.assertEqual(accepted["progress"], [50])
        self.assertFalse(accepted["removed"])
        self.assertIsNone(accepted["note"])

    def test_a_refusal_takes_the_picture_out_and_says_the_doors_sentence(self):
        for case, line in (("forbidden", "photo.png: You may not attach files here."),
                           ("notAPicture",
                            "page.svg: Only PNG, JPEG, GIF or WebP pictures can be attached.")):
            with self.subTest(case=case):
                refused = self.out[case]
                self.assertTrue(refused["removed"])
                self.assertIsNone(refused["attributes"])
                self.assertEqual(refused["note"], {"role": "alert", "lines": [line]})

    def test_nginxs_bare_413_says_the_size(self):
        self.assertTrue(self.out["tooLarge"]["removed"])
        self.assertEqual(self.out["tooLarge"]["note"]["lines"],
                         ["big.jpg: The picture is larger than 10 MB."])

    def test_a_server_error_no_network_or_a_lapsed_session_say_it_failed(self):
        for case in ("serverError", "offline", "lapsed"):
            with self.subTest(case=case):
                failed = self.out[case]
                self.assertTrue(failed["removed"])
                self.assertIsNone(failed["attributes"])
                self.assertEqual(failed["note"], {"role": "alert",
                                                  "lines": ["photo.png: Upload failed"]})

    def test_each_refusal_of_one_drop_stays_and_the_next_drop_clears_them(self):
        self.assertEqual(self.out["batch"]["lines"], ["one.png: No.", "two.svg: Not either."])
        self.assertIsNone(self.out["cleared"])
        self.assertEqual(self.out["afterward"]["attributes"],
                         {"url": "/media/trix_attachments/c.png"})
        self.assertIsNone(self.out["afterward"]["note"])

    def test_the_csrf_token_comes_from_the_form_else_the_cookie(self):
        self.assertEqual(self.out["accepted"]["csrf"], "form-token")
        self.assertEqual(self.out["cookieCsrf"], "cookie-token")

    def test_loaded_twice_it_still_sends_a_picture_once(self):
        self.assertEqual(self.out["loadedTwiceSends"], 1)

    def test_no_answer_makes_it_throw(self):
        """The package's script threw on a page that is not JSON (the
        sign-in page a lapsed session is sent to), and left the picture."""
        self.assertEqual(self.out["thrown"], [])
