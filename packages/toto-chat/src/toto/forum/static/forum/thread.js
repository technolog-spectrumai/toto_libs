(function () {
  "use strict";

  function mount() {
    var root = document.querySelector("[data-forum-thread]");
    var source = document.getElementById("forum-thread-config");
    if (!root || !source) { return; }
    var config = JSON.parse(source.textContent);
    var urls = config.urls;
    var words = root.querySelector("[data-forum-words]").dataset;
    var token = root.querySelector("[data-forum-csrf] input[name=csrfmiddlewaretoken]").value;
    var list = root.querySelector("[data-forum-replies]");
    var card = root.querySelector("[data-poll-id]");
    var older = root.querySelector("[data-forum-older]");
    var cursor = config.feed.cursor;
    var oldest = config.feed.oldest;
    var timer = null;
    var busy = false;
    var chart = null;

    function request(url, options) {
      return fetch(url, Object.assign({credentials: "same-origin"}, options || {}))
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok) { throw new Error(data.error || words.failed); }
            return data;
          });
        });
    }
    function postJson(url, data) {
      return request(url, {method: "POST", headers: {"Content-Type": "application/json",
                      "X-CSRFToken": token}, body: JSON.stringify(data || {})});
    }
    function showError(message) {
      var status = root.querySelector("[data-forum-status]");
      if (status) { status.textContent = message; status.classList.remove("hidden"); }
      else { window.alert(message); }
    }
    function clearError() {
      var status = root.querySelector("[data-forum-status]");
      if (status) { status.textContent = ""; status.classList.add("hidden"); }
    }

    function nodeFor(row) {
      var item = document.createElement("li");
      item.id = "reply-" + row.id;
      item.dataset.messageId = row.id;
      item.dataset.number = String(row.number);
      item.className = "rounded-xl border p-3";
      var head = document.createElement("div");
      head.className = "flex flex-wrap items-baseline gap-2 text-xs";
      var author = document.createElement("strong");
      author.textContent = row.sender || "";
      head.appendChild(author);
      var time = document.createElement("time");
      time.className = "opacity-60";
      time.dateTime = row.created_at;
      time.textContent = new Date(row.created_at).toLocaleString();
      head.appendChild(time);
      if (row.may_remove) {
        var remove = document.createElement("button");
        remove.type = "button";
        remove.dataset.forumRemoveMessage = row.id;
        remove.className = "ml-auto underline";
        remove.textContent = words.removeLabel;
        head.appendChild(remove);
      }
      item.appendChild(head);
      if (row.text) {
        var text = document.createElement("p");
        text.className = "mt-1 whitespace-pre-wrap break-words text-sm";
        text.textContent = row.text;
        item.appendChild(text);
      }
      if (row.image && row.image.url) {
        var link = document.createElement("a");
        link.href = row.image.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        var image = document.createElement("img");
        image.src = row.image.url;
        image.alt = words.imageLabel;
        image.loading = "lazy";
        image.className = "mt-2 max-h-64 max-w-full rounded-lg object-contain";
        link.appendChild(image);
        item.appendChild(link);
      }
      return item;
    }
    function addReply(row) {
      var previous = list.querySelector('[data-message-id="' + row.id + '"]');
      if (row.removed) {
        if (previous) { previous.textContent = words.gone; previous.className = "py-2 text-xs italic opacity-60"; }
        return;
      }
      if (previous) { return; }
      var item = nodeFor(row);
      var after = Array.from(list.children).find(function (node) {
        return Number(node.dataset.number) > row.number;
      });
      list.insertBefore(item, after || null);
    }

    function updateChart(poll) {
      var canvas = root.querySelector("[data-poll-chart]");
      if (!canvas || typeof window.Chart !== "function" || !poll.results_visible) { return; }
      var colours = ["#5999d2", "#ed9a62", "#8dbf75", "#ad84c5", "#d7b66c",
                     "#74bcb4", "#d47d97", "#8899bb", "#b9a082", "#8fb1dc"];
      var labels = poll.choices.map(function (choice) { return choice.label; });
      var values = poll.choices.map(function (choice) { return choice.ballots || 0; });
      if (chart) {
        chart.data.labels = labels;
        chart.data.datasets[0].data = values;
        chart.update();
      } else {
        chart = new window.Chart(canvas, {type: "pie", data: {
          labels: labels, datasets: [{data: values, backgroundColor: colours}]},
          options: {responsive: true, maintainAspectRatio: false,
                    plugins: {legend: {display: false}}}});
      }
    }
    function updatePoll(poll) {
      if (!poll || poll.removed) { window.location.assign(urls.community); return; }
      if (poll.status !== card.dataset.pollStatus ||
          poll.my_choice !== config.feed.poll.my_choice) {
        window.location.reload();
        return;
      }
      if (Number(card.dataset.pollSeq) >= poll.seq) { return; }
      card.dataset.pollSeq = String(poll.seq);
      config.feed.poll = poll;
      poll.choices.forEach(function (choice) {
        var line = card.querySelector('[data-choice-id="' + choice.id + '"]');
        if (!line) { return; }
        var amount = line.querySelector("[data-choice-count]");
        var bar = line.querySelector("[data-choice-bar]");
        if (amount) { amount.textContent = String(choice.ballots || 0); }
        if (bar) { bar.style.width = (poll.total ? Math.round(100 * choice.ballots / poll.total) : 0) + "%"; }
      });
      var total = card.querySelector("[data-poll-total]");
      if (total) { total.textContent = String(poll.total || 0) + " " + words.votesLabel; }
      updateChart(poll);
    }

    function schedule(delay) {
      if (timer) { clearTimeout(timer); }
      if (!document.hidden) { timer = setTimeout(refresh, delay); }
    }
    function refresh() {
      if (busy || document.hidden) { return; }
      busy = true;
      request(urls.feed + "?after=" + encodeURIComponent(cursor)).then(function (data) {
        busy = false;
        (data.messages || []).forEach(addReply);
        if (data.poll) { updatePoll(data.poll); }
        if (data.cursor > cursor) { cursor = data.cursor; }
        schedule(data.more ? 0 : Math.max(2, config.refresh_seconds) * 1000);
      }, function () { busy = false; schedule(15000); });
    }
    document.addEventListener("visibilitychange", function () {
      if (document.hidden && timer) { clearTimeout(timer); }
      if (!document.hidden) { schedule(0); }
    });
    schedule(Math.max(2, config.refresh_seconds) * 1000);
    updateChart(config.feed.poll);

    older.addEventListener("click", function () {
      if (oldest === null) { return; }
      older.disabled = true;
      request(urls.feed + "?before=" + encodeURIComponent(oldest)).then(function (data) {
        (data.messages || []).forEach(addReply);
        if (data.oldest !== null) { oldest = data.oldest; }
        older.classList.toggle("hidden", !data.more);
        older.disabled = false;
      }, function (problem) { older.disabled = false; showError(problem.message); });
    });

    var post = root.querySelector("[data-forum-post]");
    if (post) {
      var text = post.elements.text;
      var file = post.elements.image;
      var submit = post.querySelector('[type="submit"]');
      var estimate = root.querySelector("[data-forum-estimate]");
      var estimateTimer = null;
      var pending = null;
      function quote() {
        if (estimateTimer) { clearTimeout(estimateTimer); }
        estimateTimer = setTimeout(function () {
          request(urls.estimate, {method: "POST", headers: {"Content-Type": "application/json",
            "X-CSRFToken": token}, body: JSON.stringify({
              text_bytes: new TextEncoder().encode(text.value.trim()).length,
              image_bytes: file.files[0] ? file.files[0].size : 0
            })}).then(function (data) {
              estimate.textContent = data.display || "";
              submit.disabled = !data.affordable;
            }, function () { estimate.textContent = ""; });
        }, 400);
      }
      text.addEventListener("input", function () { pending = null; quote(); });
      file.addEventListener("change", function () { pending = null; quote(); });
      post.addEventListener("submit", function (event) {
        event.preventDefault();
        if (submit.disabled || (!text.value.trim() && !file.files.length)) { return; }
        clearError();
        submit.disabled = true;
        var op = pending || (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() :
          "00000000-0000-4000-8000-" + String(Date.now()).padStart(12, "0").slice(-12));
        pending = op;
        var body = new FormData(post);
        body.set("op", op);
        request(urls.post, {method: "POST", headers: {"X-CSRFToken": token}, body: body})
          .then(function (data) {
            addReply(data.message);
            pending = null;
            text.value = "";
            file.value = "";
            estimate.textContent = "";
            submit.disabled = false;
          }, function (problem) { submit.disabled = false; showError(problem.message); });
      });
    }

    card.querySelectorAll("[data-forum-vote]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var input = form.querySelector('input[name="choice"]:checked');
        if (!input) { return; }
        postJson(urls.poll_vote, {choice: Number(input.value)}).then(
          function () { window.location.reload(); }, function (error) { showError(error.message); });
      });
    });
    var reset = root.querySelector("[data-forum-reset]");
    if (reset) { reset.addEventListener("click", function () {
      postJson(urls.poll_reset).then(function () { window.location.reload(); },
                                     function (error) { showError(error.message); });
    }); }
    root.querySelectorAll("[data-forum-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.dataset.forumAction;
        if (action === "remove" && !window.confirm(words.delete)) { return; }
        button.disabled = true;
        postJson(urls["poll_" + action]).then(function () {
          if (action === "remove") { window.location.assign(urls.community); }
          else { window.location.reload(); }
        }, function (error) { button.disabled = false; showError(error.message); });
      });
    });
    list.addEventListener("click", function (event) {
      var button = event.target.closest("[data-forum-remove-message]");
      if (!button || !window.confirm(words.remove)) { return; }
      var url = urls.message_remove.replace(urls.nil, button.dataset.forumRemoveMessage);
      postJson(url).then(function (data) { addReply(data.message); },
                              function (error) { showError(error.message); });
    });
  }
  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", mount); }
  else { mount(); }
}());
