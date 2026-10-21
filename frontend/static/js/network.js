/* ==========================================================================
   CareerTwin – Network Inspector ("Connection Lab")
   --------------------------------------------------------------------------
   Measures the live chat connection: RTT, bytes, frames, transport and
   connection state. Everything is read from the browser side; the server
   panel comes from GET /api/network/stats.
   ========================================================================== */

(function () {
  "use strict";

  var PING_INTERVAL = 5000;
  var MAX_LOG_ROWS = 400;
  var MAX_SPARK_POINTS = 60;

  var stats = {
    rttSamples: [],
    currentRtt: null,
    avgRtt: null,
    minRtt: null,
    maxRtt: null,
    jitter: null,
    clockOffset: null,
    messagesUp: 0,
    messagesDown: 0,
    bytesUp: 0,
    bytesDown: 0,
    framesUp: 0,
    framesDown: 0,
    reconnects: 0
  };

  var log = [];
  var logPaused = false;
  var socket = null;
  var pingTimer = null;
  var statsTimer = null;

  /* ------------------------------------------------------------------
     Helpers
     ------------------------------------------------------------------ */
  function $(id) { return document.getElementById(id); }

  function byteLength(value) {
    try {
      return new TextEncoder().encode(typeof value === "string" ? value : JSON.stringify(value)).length;
    } catch (err) {
      return String(value || "").length;
    }
  }

  function setText(id, value) {
    var node = $(id);
    if (node) node.textContent = value;
  }

  /* ------------------------------------------------------------------
     Event log
     ------------------------------------------------------------------ */
  function logFrame(direction, event, payload, extra) {
    if (logPaused) return;
    var entry = {
      time: new Date(),
      direction: direction,
      event: event,
      size: byteLength(payload),
      seq: (extra && extra.seq) || null,
      payload: payload
    };
    log.push(entry);
    if (log.length > MAX_LOG_ROWS) log.shift();
    renderLog();
  }

  function renderLog() {
    var body = $("logBody");
    if (!body) return;
    body.textContent = "";

    log.slice().reverse().forEach(function (entry) {
      var row = document.createElement("div");
      row.className = "net-log__row";

      var time = document.createElement("span");
      time.className = "net-log__time";
      time.textContent = entry.time.toLocaleTimeString([], { hour12: false });

      var dir = document.createElement("span");
      dir.className = "net-log__dir net-log__dir--" + entry.direction;
      dir.textContent = entry.direction === "up" ? "↑" : "↓";

      var name = document.createElement("span");
      name.className = "net-log__event";
      name.textContent = entry.event;

      var size = document.createElement("span");
      size.className = "net-log__size";
      size.textContent = entry.size + " B";

      var seq = document.createElement("span");
      seq.className = "net-log__seq";
      seq.textContent = entry.seq === null ? "" : "#" + entry.seq;

      row.appendChild(time);
      row.appendChild(dir);
      row.appendChild(name);
      row.appendChild(size);
      row.appendChild(seq);

      row.addEventListener("click", function () {
        body.querySelectorAll(".net-log__row").forEach(function (r) {
          r.classList.remove("is-selected");
        });
        row.classList.add("is-selected");
        var detail = $("logDetail");
        if (detail) {
          detail.textContent = JSON.stringify(entry.payload, null, 2);
        }
      });

      body.appendChild(row);
    });
  }

  function exportCsv() {
    var rows = [["time", "direction", "event", "bytes", "seq", "payload"]];
    log.forEach(function (entry) {
      rows.push([
        entry.time.toISOString(),
        entry.direction,
        entry.event,
        entry.size,
        entry.seq === null ? "" : entry.seq,
        typeof entry.payload === "string" ? entry.payload : JSON.stringify(entry.payload)
      ]);
    });
    var csv = rows.map(function (row) {
      return row.map(function (cell) {
        var text = String(cell === null ? "" : cell);
        return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
      }).join(",");
    }).join("\n");

    var blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = "careertwin-network-" + Date.now() + ".csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  /* ------------------------------------------------------------------
     RTT
     ------------------------------------------------------------------ */
  function recordRtt(rtt) {
    stats.currentRtt = rtt;
    stats.rttSamples.push(rtt);
    if (stats.rttSamples.length > MAX_SPARK_POINTS) stats.rttSamples.shift();

    var sum = 0;
    stats.minRtt = Infinity;
    stats.maxRtt = -Infinity;
    stats.rttSamples.forEach(function (value) {
      sum += value;
      if (value < stats.minRtt) stats.minRtt = value;
      if (value > stats.maxRtt) stats.maxRtt = value;
    });
    stats.avgRtt = sum / stats.rttSamples.length;

    // Jitter: mean absolute difference between consecutive RTT samples
    // (RFC 3550 style).
    if (stats.rttSamples.length > 1) {
      var jitterSum = 0;
      for (var i = 1; i < stats.rttSamples.length; i++) {
        jitterSum += Math.abs(stats.rttSamples[i] - stats.rttSamples[i - 1]);
      }
      stats.jitter = jitterSum / (stats.rttSamples.length - 1);
    } else {
      stats.jitter = 0;
    }

    setText("rttCurrent", stats.currentRtt.toFixed(1) + " ms");
    setText("rttAvg", stats.avgRtt.toFixed(1) + " ms");
    setText("rttMin", stats.minRtt === Infinity ? "–" : stats.minRtt.toFixed(1) + " ms");
    setText("rttMax", stats.maxRtt === -Infinity ? "–" : stats.maxRtt.toFixed(1) + " ms");
    setText("rttJitter", stats.jitter.toFixed(1) + " ms");
    drawSparkline();
  }

  function drawSparkline() {
    var svg = $("rttSparkline");
    if (!svg) return;
    var samples = stats.rttSamples;
    if (samples.length < 2) return;

    var width = 240;
    var height = 48;
    var max = Math.max.apply(null, samples);
    var min = Math.min.apply(null, samples);
    var range = Math.max(1, max - min);

    var points = samples.map(function (value, index) {
      var x = (index / (samples.length - 1)) * width;
      var y = height - ((value - min) / range) * (height - 8) - 4;
      return x.toFixed(1) + "," + y.toFixed(1);
    });

    svg.textContent = "";
    var ns = "http://www.w3.org/2000/svg";

    var area = document.createElementNS(ns, "polygon");
    area.setAttribute("class", "sparkline__area");
    area.setAttribute("points", "0," + height + " " + points.join(" ") + " " + width + "," + height);
    svg.appendChild(area);

    var line = document.createElementNS(ns, "polyline");
    line.setAttribute("class", "sparkline__line");
    line.setAttribute("points", points.join(" "));
    svg.appendChild(line);
  }

  function ping() {
    if (!socket || !socket.connected) return;
    var t = performance.now();
    socket.emit("rtt:ping", { t: t }, function (response) {
      if (!response) return;
      var rtt = performance.now() - t;
      recordRtt(rtt);
      if (typeof response.server_time === "number") {
        // Clock offset: server clock minus local clock at the moment the
        // answer was produced. We do not know the exact one-way delay, so we
        // approximate it with half the RTT.
        stats.clockOffset = response.server_time - (Date.now() + rtt / 2);
        setText("clockOffset", (stats.clockOffset >= 0 ? "+" : "") + stats.clockOffset.toFixed(0) + " ms");
      }
      logFrame("down", "rtt:pong", { rtt: Math.round(rtt) });
    });
    logFrame("up", "rtt:ping", { t: Math.round(t) });
  }

  /* ------------------------------------------------------------------
     Server panel
     ------------------------------------------------------------------ */
  function loadServerStats() {
    window.CT.fetchJSON("/api/network/stats")
      .then(function (data) {
        setText("srvUptime", formatDuration(data.uptime_seconds));
        setText("srvWs", String(data.websocket_connections || 0));
        setText("srvTcp", String(data.tcp_connections || 0));
        setText("srvFrames", String(data.frames_in || 0) + " in / " + String(data.frames_out || 0) + " out");
        setText("srvBytes", formatBytes(data.bytes_in || 0) + " in / " + formatBytes(data.bytes_out || 0) + " out");
        setText("srvOnline", String(data.online_users || 0));

        var list = $("srvConnections");
        if (list) {
          list.textContent = "";
          (data.your_connections || []).forEach(function (conn) {
            var row = document.createElement("div");
            row.className = "compat-table__row";
            var left = document.createElement("span");
            left.textContent = conn.transport + " · " + conn.connection_id;
            var right = document.createElement("span");
            right.className = "muted";
            right.textContent = conn.frames_in + "↓ / " + conn.frames_out + "↑";
            row.appendChild(left);
            row.appendChild(right);
            list.appendChild(row);
          });
          if (!(data.your_connections || []).length) {
            var empty = document.createElement("div");
            empty.className = "compat-table__row";
            empty.textContent = "No live connections for this account.";
            list.appendChild(empty);
          }
        }
      })
      .catch(function () { /* keep the previous values */ });
  }

  function formatBytes(value) {
    if (!value) return "0 B";
    var units = ["B", "KB", "MB", "GB"];
    var index = 0;
    var n = value;
    while (n >= 1024 && index < units.length - 1) {
      n = n / 1024;
      index++;
    }
    return n.toFixed(index === 0 ? 0 : 1) + " " + units[index];
  }

  function formatDuration(seconds) {
    seconds = Math.floor(seconds || 0);
    var h = Math.floor(seconds / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = seconds % 60;
    if (h) return h + "h " + m + "m";
    if (m) return m + "m " + s + "s";
    return s + "s";
  }

  /* ------------------------------------------------------------------
     Connection state
     ------------------------------------------------------------------ */
  function updateConnectionCard() {
    var connected = !!(socket && socket.connected);
    setText("netState", connected ? "Connected" : "Disconnected");
    var card = $("netStateCard");
    if (card) card.classList.toggle("net-card--accent", connected);

    var transport = "—";
    if (socket) {
      var engine = socket.io && socket.io.engine;
      if (engine) transport = engine.transport && engine.transport.name ? engine.transport.name : "unknown";
    }
    setText("netTransport", transport);
    setText("netReconnects", String(stats.reconnects));
    updateCounters();
  }

  function updateCounters() {
    setText("msgCount", stats.messagesUp + " ↑ / " + stats.messagesDown + " ↓");
    setText("byteCount", formatBytes(stats.bytesUp) + " ↑ / " + formatBytes(stats.bytesDown) + " ↓");
  }

  /* ------------------------------------------------------------------
     Wiring
     ------------------------------------------------------------------ */
  function attach() {
    socket = window.CTChat ? window.CTChat.connect() : null;
    if (!socket) return;

    // Log every outbound chat frame.
    var originalEmit = socket.emit.bind(socket);
    socket.emit = function (event) {
      var args = Array.prototype.slice.call(arguments);
      if (event !== "rtt:ping") {
        stats.framesUp += 1;
        stats.bytesUp += byteLength(args[1]);
        logFrame("up", event, args[1] === undefined ? null : args[1]);
        if (event === "message:send" && args[1]) {
          stats.messagesUp += 1;
        }
        updateCounters();
      }
      return originalEmit.apply(null, args);
    };

    socket.on("connect", function () {
      updateConnectionCard();
      logFrame("down", "connect", { id: socket.id });
      startPing();
    });

    socket.on("disconnect", function (reason) {
      updateConnectionCard();
      stats.reconnects += 1;
      setText("netReconnects", String(stats.reconnects));
      logFrame("down", "disconnect", { reason: reason });
      stopPing();
    });

    socket.on("connect_error", function (err) {
      updateConnectionCard();
      logFrame("down", "connect_error", { message: String(err) });
    });

    ["message:new", "message:read", "message:delivered", "typing", "presence:update", "notify:summary", "error"].forEach(function (event) {
      socket.on(event, function (payload) {
        stats.framesDown += 1;
        stats.bytesDown += byteLength(payload);
        if (event === "message:new") stats.messagesDown += 1;
        var seq = payload && payload.message ? payload.message.seq : null;
        logFrame("down", event, payload, { seq: seq });
        updateCounters();
      });
    });

    updateConnectionCard();
    if (socket.connected) startPing();
  }

  function startPing() {
    stopPing();
    ping();
    pingTimer = window.setInterval(ping, PING_INTERVAL);
  }

  function stopPing() {
    if (pingTimer) {
      window.clearInterval(pingTimer);
      pingTimer = null;
    }
  }

  function initSimulation() {
    var enabled = $("simEnabled");
    var latency = $("simLatency");
    var drop = $("simDrop");
    var latencyValue = $("simLatencyValue");
    var dropValue = $("simDropValue");

    function apply() {
      var options = {
        latencyMs: enabled && enabled.checked ? Number(latency.value) : 0,
        dropPercent: enabled && enabled.checked ? Number(drop.value) : 0
      };
      if (window.CTChat && window.CTChat.setSimulation) {
        window.CTChat.setSimulation(options);
      }
      latencyValue.textContent = options.latencyMs + " ms";
      dropValue.textContent = options.dropPercent + " %";
      var note = $("simStatus");
      if (note) {
        note.textContent = enabled && enabled.checked
          ? "Simulation active: " + options.latencyMs + " ms latency, " +
            options.dropPercent + "% of frames dropped."
          : "Simulation off – frames travel normally.";
      }
    }

    if (enabled) {
      enabled.addEventListener("change", apply);
      latency.addEventListener("input", apply);
      drop.addEventListener("input", apply);
    }
    apply();
  }

  function initLogControls() {
    var pause = $("logPause");
    var clear = $("logClear");
    var exportBtn = $("logExport");

    if (pause) {
      pause.addEventListener("click", function () {
        logPaused = !logPaused;
        pause.textContent = logPaused ? "Resume" : "Pause";
        pause.setAttribute("aria-pressed", String(logPaused));
      });
    }
    if (clear) {
      clear.addEventListener("click", function () {
        log = [];
        renderLog();
        var detail = $("logDetail");
        if (detail) detail.textContent = "Select a row to inspect its payload.";
      });
    }
    if (exportBtn) exportBtn.addEventListener("click", exportCsv);
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!$("netState")) return;

    attach();
    initSimulation();
    initLogControls();
    loadServerStats();
    statsTimer = window.setInterval(loadServerStats, 5000);
  });

  window.addEventListener("beforeunload", function () {
    stopPing();
    if (statsTimer) window.clearInterval(statsTimer);
  });
})();
