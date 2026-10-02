/* The Trix editor's attachments, sent to the platform (2026-10-02).
 *
 * In place of django-trix-editor's own script, which acted on a 200 alone:
 * a picture the door refused (not a picture, too large, a sender who may
 * not attach) stayed in the editor at its progress bar with nothing said,
 * and the post kept a picture nobody could see. Here a refusal takes the
 * picture out again and says why under the editor, in the door's own
 * sentence, or in one of the two the page gives when the door said none
 * (nginx's 413, a server error, no network).
 *
 * The <script> tag that loads this carries the door's address and those two
 * sentences (toto.verbena.widgets.TrixUploadScript), so nothing here needs
 * a translation of its own. Loaded once per page however many editors it
 * has: two copies would send every picture twice.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script || window.totoTrixUpload) {
    return;
  }
  var door = {
    url: script.getAttribute("data-upload-url"),
    failed: script.getAttribute("data-upload-failed"),
    tooLarge: script.getAttribute("data-upload-too-large"),
  };
  window.totoTrixUpload = door;

  // Per editor: the line under it, and how many of its pictures are on
  // their way. A drop of several pictures is one batch: the line is
  // cleared when a new batch starts, so each refusal of a batch stays.
  var notes = new WeakMap();
  var pending = new WeakMap();

  function csrfToken(editor) {
    var form = editor.closest ? editor.closest("form") : null;
    var field = form ? form.querySelector('input[name="csrfmiddlewaretoken"]') : null;
    if (field && field.value) {
      return field.value;
    }
    var match = /(?:^|;\s*)csrftoken=([^;]*)/.exec(document.cookie || "");
    return match ? decodeURIComponent(match[1]) : "";
  }

  function noteFor(editor) {
    var note = notes.get(editor);
    if (!note) {
      note = document.createElement("p");
      note.setAttribute("role", "alert");
      note.setAttribute("data-trix-upload-refusal", "");
      note.hidden = true;
      // Inline, so it reads the same on the platform's pages and in the
      // admin, in either theme: the page's own text colour, a red rule.
      note.style.cssText = "margin:0.5rem 0 0;padding:0.25rem 0.75rem;"
        + "border-left:3px solid #dc2626;font-size:0.875rem;font-weight:600;";
      editor.insertAdjacentElement("afterend", note);
      notes.set(editor, note);
    }
    return note;
  }

  function refuse(editor, attachment, sentence) {
    attachment.remove();
    var note = noteFor(editor);
    var line = document.createElement("span");
    line.style.cssText = "display:block;";
    line.textContent = (attachment.file && attachment.file.name ? attachment.file.name + ": " : "")
      + (sentence || door.failed);
    note.appendChild(line);
    note.hidden = false;
  }

  function sentenceOf(xhr) {
    if (xhr.status === 413) {
      return door.tooLarge;
    }
    try {
      var answer = JSON.parse(xhr.responseText);
      if (answer && typeof answer.error === "string" && answer.error) {
        return answer.error;
      }
    } catch (ignored) {
      // An HTML page — nginx's, or the sign-in page a lapsed session is sent to.
    }
    return door.failed;
  }

  function urlOf(xhr) {
    if (xhr.status !== 200) {
      return null;
    }
    try {
      var answer = JSON.parse(xhr.responseText);
      return answer && typeof answer.attachment_url === "string" && answer.attachment_url
        ? answer.attachment_url : null;
    } catch (ignored) {
      return null;
    }
  }

  function upload(editor, attachment) {
    var count = pending.get(editor) || 0;
    if (count === 0 && notes.has(editor)) {
      var note = notes.get(editor);
      note.textContent = "";
      note.hidden = true;
    }
    pending.set(editor, count + 1);

    var finished = false;
    function finish(url, sentence) {
      if (finished) {
        return;
      }
      finished = true;
      pending.set(editor, Math.max(0, (pending.get(editor) || 1) - 1));
      if (url) {
        attachment.setAttributes({url: url});
      } else {
        refuse(editor, attachment, sentence);
      }
    }

    var form = new FormData();
    form.append("Content-Type", attachment.file.type);
    form.append("file", attachment.file);
    var xhr = new XMLHttpRequest();
    xhr.open("POST", door.url, true);
    xhr.setRequestHeader("X-CSRFToken", csrfToken(editor));
    xhr.upload.addEventListener("progress", function (event) {
      if (event.lengthComputable && event.total) {
        attachment.setUploadProgress(event.loaded / event.total * 100);
      }
    });
    xhr.addEventListener("load", function () {
      var url = urlOf(xhr);
      finish(url, url ? null : sentenceOf(xhr));
    });
    ["error", "abort", "timeout"].forEach(function (type) {
      xhr.addEventListener(type, function () {
        finish(null, door.failed);
      });
    });
    xhr.send(form);
  }

  addEventListener("trix-attachment-add", function (event) {
    if (event.attachment && event.attachment.file) {
      upload(event.target, event.attachment);
    }
  });
})();
