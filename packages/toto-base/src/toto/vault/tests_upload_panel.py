"""The upload queue behind the vault list's Upload (2026-10-04):
``vault/upload_panel.js`` as it ships, run in node with a faked
XMLHttpRequest — two at a time, a bar and a speed per file, Cancel, and the
door's own sentence for a refusal.

    manage.py test toto.vault.tests_upload_panel
"""

import json
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

_NODE = shutil.which("node")

_HARNESS = r"""
const {createUploadQueue, refusalText, formatBytes, formatSpeed} = require(process.argv[1]);
const out = {};
const sent = [];
class Form { constructor() { this.fields = {}; } append(k, v) { this.fields[k] = v; } }
class XHR {
  constructor() { this.upload = {}; this.headers = {}; sent.push(this); }
  open(method, url) { this.method = method; this.url = url; }
  setRequestHeader(k, v) { this.headers[k] = v; }
  send(form) { this.form = form; }
  abort() { this.onabort(); }
}
let clock = 1000;
const drawn = [], landed = [];
const queue = createUploadQueue({url: "/vault/api/files/upload/", csrf: () => "tok", XHR, FormData: Form,
  now: () => clock, concurrency: 2,
  messages: {network: "Network error.", refused: "The server refused this file",
             tooLarge: "Too large.", cancelled: "Cancelled"},
  onChange: (items) => drawn.push(items), onDone: (id, target) => landed.push([id, target.directoryId])});
const target = {bucketSlug: "work", directoryId: 7};
const files = ["a.png", "b.docx", "c.bin", "d.txt", "e.txt"].map((name, n) => ({name, size: 1000 * (n + 1)}));
const ids = files.map(f => queue.add(f, target));
const now = () => queue.snapshot().map(i => i.status);
out.atStart = now();
out.inFlight = sent.length;
out.request = [sent[0].method, sent[0].url, sent[0].headers["X-CSRFToken"], sent[0].form.fields.title,
               sent[0].form.fields.bucket_slug, sent[0].form.fields.directory_id];
clock += 500; sent[0].upload.onprogress({lengthComputable: true, loaded: 500, total: 1000});
out.halfway = queue.snapshot()[0];
clock += 500; sent[0].upload.onprogress({lengthComputable: true, loaded: 1000, total: 1000});
out.allSentNotAnswered = queue.snapshot()[0].percent;
sent[0].status = 201; sent[0].responseText = JSON.stringify({id: 41}); sent[0].onload();
out.afterFirst = now();
sent[1].status = 400; sent[1].responseText = JSON.stringify({error: "Office files are not accepted."}); sent[1].onload();
out.refused = queue.snapshot()[1];
out.afterRefusal = now();
queue.cancel(ids[4]);              // still waiting
queue.cancel(ids[2]);              // travelling
out.afterCancel = now();
out.cancelled = [queue.snapshot()[2].message, queue.snapshot()[4].message];
sent[3].onerror();
out.network = queue.snapshot()[3].message;
out.busy = queue.busy();
out.landed = landed;
out.started = sent.length;
queue.clearFinished();
out.cleared = queue.snapshot().length;
out.drawnLast = drawn[drawn.length - 1].length;
out.texts = [refusalText(413, "<html>Request Entity Too Large</html>", {tooLarge: "Too large."}),
             refusalText(429, JSON.stringify({error: "Quota spent."}), {}),
             refusalText(402, JSON.stringify({detail: "No funds."}), {}),
             refusalText(500, "boom", {refused: "Refused"}), refusalText(0, "", {network: "Net"})];
out.sizes = [formatBytes(512), formatBytes(1536), formatBytes(5 * 1024 * 1024), formatSpeed(0),
             formatSpeed(2 * 1024 * 1024)];
console.log(JSON.stringify(out));
"""


@skipUnless(_NODE, "node is not installed")
class UploadQueueTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.script = finders.find("vault/upload_panel.js")
        done = subprocess.run([_NODE, "-e", _HARNESS, cls.script], capture_output=True,
                              text=True, timeout=60)
        if done.returncode != 0:
            raise AssertionError(f"node failed: {done.stderr}")
        cls.out = json.loads(done.stdout.strip().splitlines()[-1])

    def test_two_travel_at_a_time_and_the_rest_wait(self):
        self.assertEqual(self.out["atStart"],
                         ["uploading", "uploading", "queued", "queued", "queued"])
        self.assertEqual(self.out["inFlight"], 2)
        self.assertEqual(self.out["afterFirst"],
                         ["done", "uploading", "uploading", "queued", "queued"])

    def test_each_goes_to_the_upload_door_with_the_token_and_its_folder(self):
        self.assertEqual(self.out["request"],
                         ["POST", "/vault/api/files/upload/", "tok", "a.png", "work", 7])

    def test_a_file_has_a_percent_and_a_speed(self):
        half = self.out["halfway"]
        self.assertEqual((half["percent"], half["status"]), (50, "uploading"))
        self.assertEqual(half["speed"], 1000)
        self.assertEqual(half["speedText"], "1000 B/s")
        # The last byte leaving is not the door's answer.
        self.assertEqual(self.out["allSentNotAnswered"], 99)

    def test_a_refusal_is_said_on_its_own_file_in_the_doors_words_and_the_rest_go_on(self):
        self.assertEqual((self.out["refused"]["status"], self.out["refused"]["message"]),
                         ("refused", "Office files are not accepted."))
        self.assertEqual(self.out["afterRefusal"],
                         ["done", "refused", "uploading", "uploading", "queued"])

    def test_cancel_stops_a_waiting_file_and_a_travelling_one(self):
        self.assertEqual(self.out["afterCancel"],
                         ["done", "refused", "cancelled", "uploading", "cancelled"])
        self.assertEqual(self.out["cancelled"], ["Cancelled", "Cancelled"])
        self.assertEqual(self.out["started"], 4)       # the cancelled waiter never left

    def test_a_network_fault_and_the_end(self):
        self.assertEqual(self.out["network"], "Network error.")
        self.assertFalse(self.out["busy"])
        self.assertEqual(self.out["landed"], [[41, 7]])
        self.assertEqual((self.out["cleared"], self.out["drawnLast"]), (0, 0))

    def test_what_a_refusal_says(self):
        self.assertEqual(self.out["texts"], ["Too large.", "Quota spent.", "No funds.",
                                             "Refused (500)", "Net"])
        self.assertEqual(self.out["sizes"], ["512 B", "1.5 KB", "5.0 MB", "", "2.0 MB/s"])

    def test_the_script_touches_no_element_and_holds_no_server_value(self):
        source = Path(self.script).read_text(encoding="utf-8")
        for word in ("innerHTML", "document.", "{{", "{%", "eval("):
            self.assertNotIn(word, source)
