/* ==========================================================================
   Resume analyser page
   Uses the shared CT.fetchJSON helper so the CSRF token is always sent.
   ========================================================================== */

(function () {
  "use strict";

  var fileInput = document.getElementById("resumeFile");
  var dropzone = document.getElementById("dropzone");
  var fileName = document.getElementById("fileName");
  var analyzeBtn = document.getElementById("analyzeBtn");
  var status = document.getElementById("uploadStatus");
  var resultCard = document.getElementById("resultCard");

  if (!fileInput || !dropzone) return;

  var MAX_BYTES = 16 * 1024 * 1024;

  function setStatus(message) {
    if (status) status.textContent = message || "";
  }

  function showFile(file) {
    if (!file) {
      fileName.hidden = true;
      fileName.textContent = "";
      return;
    }
    fileName.hidden = false;
    fileName.textContent =
      file.name + " — " + (file.size / 1024).toFixed(1) + " KB";
  }

  /* --- Drag & drop --------------------------------------------------- */
  ["dragenter", "dragover"].forEach(function (evt) {
    dropzone.addEventListener(evt, function (e) {
      e.preventDefault();
      dropzone.classList.add("is-over");
    });
  });

  ["dragleave", "drop"].forEach(function (evt) {
    dropzone.addEventListener(evt, function (e) {
      e.preventDefault();
      dropzone.classList.remove("is-over");
    });
  });

  dropzone.addEventListener("drop", function (e) {
    if (e.dataTransfer && e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      showFile(fileInput.files[0]);
    }
  });

  dropzone.addEventListener("click", function () {
    fileInput.click();
  });

  dropzone.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });

  fileInput.addEventListener("change", function () {
    showFile(fileInput.files[0]);
  });

  /* --- Rendering ------------------------------------------------------ */
  function renderSkills(skills) {
    var list = document.getElementById("skillsList");
    list.textContent = "";
    if (!skills || !skills.length) {
      var li = document.createElement("li");
      li.className = "muted small";
      li.textContent = "No skills detected. Try a more complete resume.";
      list.appendChild(li);
      return;
    }
    skills.forEach(function (skill) {
      var li = document.createElement("li");
      var chip = document.createElement("span");
      chip.className = "chip chip--has";
      chip.textContent = skill;
      li.appendChild(chip);
      list.appendChild(li);
    });
  }

  function renderSuggestions(suggestions) {
    var list = document.getElementById("suggestionsList");
    list.textContent = "";
    if (!suggestions || !suggestions.length) {
      var li = document.createElement("li");
      li.className = "muted small";
      li.textContent = "No suggestions — your resume covers the common keywords.";
      list.appendChild(li);
      return;
    }
    suggestions.forEach(function (text) {
      var li = document.createElement("li");
      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("aria-hidden", "true");
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.setAttribute("fill", "none");
      svg.setAttribute("stroke", "currentColor");
      svg.setAttribute("stroke-width", "2");
      svg.setAttribute("stroke-linecap", "round");
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", "M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z");
      svg.appendChild(path);
      li.appendChild(svg);
      var span = document.createElement("span");
      span.textContent = text;
      li.appendChild(span);
      list.appendChild(li);
    });
  }

  function setRing(percent) {
    var ring = document.querySelector(".progress-ring");
    if (!ring) return;
    var value = ring.querySelector(".progress-ring__value");
    var label = ring.querySelector(".progress-ring__label");
    if (value) {
      var r = parseFloat(value.getAttribute("r"));
      var c = 2 * Math.PI * r;
      var offset = c - (c * percent) / 100;
      value.setAttribute("stroke-dashoffset", String(offset));
    }
    if (label) {
      label.childNodes[0].nodeValue = percent + "%";
    }
  }

  function renderResult(data, isLatest) {
    if (!data) return;
    setRing(data.score || 0);
    renderSkills(data.skills_found || []);
    renderSuggestions(data.suggestions || []);

    var subtitle = document.getElementById("resultSubtitle");
    if (subtitle) subtitle.textContent = data.filename || "";

    var uploadedAt = document.getElementById("uploadedAt");
    if (uploadedAt) uploadedAt.textContent = data.uploaded_at ? "Uploaded " + data.uploaded_at : "";

    if (resultCard) resultCard.hidden = false;
    if (isLatest) renderLastAnalysis(data);
  }

  function renderLastAnalysis(data) {
    var box = document.getElementById("lastAnalysis");
    if (!box) return;
    box.textContent = "";

    var name = document.createElement("div");
    name.className = "truncate";
    name.style.fontWeight = "600";
    name.textContent = data.filename || "Resume";

    var score = document.createElement("div");
    score.className = "small muted";
    score.textContent = "Score " + (data.score || 0) + "%" +
      (data.uploaded_at ? " · " + data.uploaded_at : "");

    box.appendChild(name);
    box.appendChild(score);
  }

  /* --- Actions --------------------------------------------------------- */
  function analyze() {
    if (!fileInput.files || !fileInput.files.length) {
      if (window.CT) window.CT.toast("Please choose a resume first.", "warning");
      return;
    }
    if (fileInput.files[0].size > MAX_BYTES) {
      if (window.CT) window.CT.toast("That file is larger than 16 MB.", "error");
      return;
    }

    var formData = new FormData();
    formData.append("resume", fileInput.files[0]);

    analyzeBtn.classList.add("is-loading");
    analyzeBtn.disabled = true;
    setStatus("Uploading and analysing…");

    window.CT.fetchJSON("/api/resume/analyze", { method: "POST", body: formData })
      .then(function (data) {
        renderResult(data, true);
        setStatus("Done.");
        if (window.CT) window.CT.toast("Resume analysed: " + data.score + "%", "success");
      })
      .catch(function (error) {
        setStatus("");
        if (window.CT) window.CT.toast(error.message || "Resume analysis failed.", "error");
      })
      .finally(function () {
        analyzeBtn.classList.remove("is-loading");
        analyzeBtn.disabled = false;
      });
  }

  if (analyzeBtn) analyzeBtn.addEventListener("click", analyze);

  /* --- Load the most recent analysis ----------------------------------- */
  window.CT.fetchJSON("/api/resume/latest")
    .then(function (data) {
      if (data && data.resume) {
        renderResult(data.resume, true);
        if (resultCard) resultCard.hidden = false;
      }
    })
    .catch(function () { /* nothing to show */ });
})();
