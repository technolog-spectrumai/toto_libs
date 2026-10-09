(function () {
  "use strict";
  var root = document.querySelector("[data-forum-feed]");
  if (!root) { return; }
  var csrf = root.querySelector("[data-forum-csrf] input[name=csrfmiddlewaretoken]");
  var token = csrf ? csrf.value : "";

  function send(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRFToken": token},
      body: JSON.stringify(body || {})
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) { throw new Error(data.error || "The request failed."); }
        return data;
      });
    });
  }

  var dialog = root.querySelector("[data-forum-create-dialog]");
  var open = root.querySelector("[data-forum-create-open]");
  if (open && dialog) {
    open.addEventListener("click", function () {
      dialog.dispatchEvent(new CustomEvent("forum-create"));
    });
  }
  var create = root.querySelector("[data-forum-create]");
  if (create) {
    create.addEventListener("submit", function (event) {
      event.preventDefault();
      var error = root.querySelector("[data-forum-create-error]");
      var choice = create.elements.community;
      var deadline = new Date(create.elements.closes_at.value);
      var button = create.querySelector('[type="submit"]');
      if (isNaN(deadline.getTime())) { return; }
      button.disabled = true;
      error.textContent = "";
      error.classList.add("hidden");
      send(choice.value, {
        title: create.elements.title.value,
        description: create.elements.description.value,
        options: create.elements.options.value,
        closes_at: deadline.toISOString(),
        visibility: create.elements.visibility.value
      }).then(function (data) {
        window.location.assign(data.thread_url);
      }, function (problem) {
        button.disabled = false;
        error.textContent = problem.message;
        error.classList.remove("hidden");
      });
    });
  }

  root.querySelectorAll("[data-forum-vote]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var selected = form.querySelector('input[name="choice"]:checked');
      if (!selected) { return; }
      var button = form.querySelector('[type="submit"]');
      if (button) { button.disabled = true; }
      send(form.action, {choice: Number(selected.value)}).then(
        function () { window.location.reload(); },
        function (problem) { if (button) { button.disabled = false; } window.alert(problem.message); }
      );
    });
  });
  root.querySelectorAll("[data-forum-reset]").forEach(function (button) {
    button.addEventListener("click", function () {
      button.disabled = true;
      send(button.dataset.url).then(
        function () { window.location.reload(); },
        function (problem) { button.disabled = false; window.alert(problem.message); }
      );
    });
  });
}());
