/* ==========================================================================
   CareerTwin – chat client
   --------------------------------------------------------------------------
   Owns a single Socket.IO connection to the /chat namespace and exposes it
   through `window.CTChat` so the Network Inspector can measure the very same
   connection.

   Safety rule: message bodies are always inserted with `textContent`.
   `innerHTML` is never used with user-supplied data.
   ========================================================================== */

(function () {
  "use strict";

  var RECONNECT_STEPS = [1000, 2000, 4000, 8000, 15000, 30000];
  var MAX_CHARS = 2000;

  var state = {
    socket: null,
    connected: false,
    reconnectAttempt: 0,
    reconnectTimer: null,
    conversationId: null,
    conversations: [],
    messages: {},          // conversationId -> [message]
    lastSeq: {},           // conversationId -> highest seq seen
    pending: {},           // clientMsgId -> {conversationId, body, el}
    queue: [],             // messages queued while offline
    typingUsers: {},
    listeners: {},
    intentionallyClosed: false,
    // Client-side network simulation (used by the Network Inspector).
    // Both values are 0 / "off" by default.
    simulation: { latencyMs: 0, dropPercent: 0 }
  };

  /* ------------------------------------------------------------------
     Client-side network simulation
     ------------------------------------------------------------------
     "latency" delays every outbound chat frame, "drop" discards frames
     entirely. Dropping a frame is what makes the retry / idempotency logic
     visible: the sender never gets an ack, the optimistic bubble turns into
     a retryable failure, and re-sending the same `client_msg_id` does not
     create a duplicate.
     ------------------------------------------------------------------ */
  function setSimulation(options) {
    state.simulation.latencyMs = Math.max(0, Number(options.latencyMs) || 0);
    state.simulation.dropPercent = Math.min(100, Math.max(0, Number(options.dropPercent) || 0));
    emitLocal("simulation", Object.assign({}, state.simulation));
    return state.simulation;
  }

  function shouldDrop() {
    return state.simulation.dropPercent > 0 &&
      Math.random() * 100 < state.simulation.dropPercent;
  }

  function withLatency(fn) {
    if (state.simulation.latencyMs > 0) {
      window.setTimeout(fn, state.simulation.latencyMs);
    } else {
      fn();
    }
  }

  /* ------------------------------------------------------------------
     Tiny event bus so other pages (the inspector) can observe the socket.
     ------------------------------------------------------------------ */
  function on(event, handler) {
    (state.listeners[event] = state.listeners[event] || []).push(handler);
  }

  function emitLocal(event, payload) {
    (state.listeners[event] || []).forEach(function (handler) {
      try { handler(payload); } catch (err) { console.error(err); }
    });
  }

  function uuid() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : ((r & 0x3) | 0x8);
      return v.toString(16);
    });
  }

  /* ------------------------------------------------------------------
     Connection
     ------------------------------------------------------------------ */
  function setBanner(kind, text) {
    var banner = document.getElementById("connBanner");
    if (!banner) return;
    banner.className = "conn-banner conn-banner--" + kind;
    var label = banner.querySelector("[data-banner-text]");
    if (label) label.textContent = text;
    banner.hidden = false;
  }

  function updateBanner() {
    if (state.connected) {
      setBanner("connected", "Connected");
    } else if (state.reconnectAttempt > 0) {
      var wait = RECONNECT_STEPS[Math.min(state.reconnectAttempt - 1, RECONNECT_STEPS.length - 1)];
      var seconds = Math.round(wait / 1000);
      setBanner("connecting", "Reconnecting in " + seconds + "s…");
    } else {
      setBanner("offline", "Offline — messages will send when you're back");
    }
    emitLocal("connection", { connected: state.connected });
  }

  function connect() {
    if (state.socket) return state.socket;

    if (typeof io === "undefined") {
      console.error("Socket.IO client is not loaded.");
      return null;
    }

    state.socket = io("/chat", {
      transports: ["websocket", "polling"],
      reconnection: false,           // we drive reconnection ourselves
      autoConnect: true,
      withCredentials: true
    });

    state.socket.on("connect", function () {
      state.connected = true;
      state.reconnectAttempt = 0;
      updateBanner();
      emitLocal("connect", {});
      flushQueue();
      resync();
    });

    state.socket.on("disconnect", function (reason) {
      state.connected = false;
      updateBanner();
      emitLocal("disconnect", { reason: reason });
      if (!state.intentionallyClosed) scheduleReconnect();
    });

    state.socket.on("connect_error", function () {
      state.connected = false;
      updateBanner();
      if (!state.intentionallyClosed) scheduleReconnect();
    });

    state.socket.on("message:new", function (payload) {
      handleIncoming(payload);
    });

    state.socket.on("message:read", function (payload) {
      emitLocal("message:read", payload);
      if (payload && payload.conversation_id === state.conversationId) {
        markRead(payload.up_to_seq);
      }
    });

    state.socket.on("message:delivered", function (payload) {
      emitLocal("message:delivered", payload);
    });

    state.socket.on("typing", function (payload) {
      if (!payload) return;
      state.typingUsers[payload.conversation_id] = payload.user_id;
      emitLocal("typing", payload);
      renderTyping(payload.conversation_id);
    });

    state.socket.on("presence:update", function (payload) {
      emitLocal("presence", payload);
      renderConversations();
    });

    state.socket.on("notify:summary", function (payload) {
      emitLocal("notify", payload);
    });

    state.socket.on("error", function (payload) {
      console.warn("server error", payload);
      if (window.CT) {
        window.CT.toast((payload && payload.message) || "Chat error.", "error");
      }
    });

    updateBanner();
    return state.socket;
  }

  function scheduleReconnect() {
    if (state.reconnectTimer || state.intentionallyClosed) return;
    var index = Math.min(state.reconnectAttempt, RECONNECT_STEPS.length - 1);
    var wait = RECONNECT_STEPS[index];
    // Jitter: up to +25 % so many clients do not retry in lockstep.
    var delay = wait + Math.random() * wait * 0.25;
    state.reconnectAttempt += 1;
    updateBanner();
    state.reconnectTimer = window.setTimeout(function () {
      state.reconnectTimer = null;
      if (state.socket) state.socket.connect();
      else connect();
    }, delay);
  }

  function flushQueue() {
    if (!state.queue.length) return;
    var queued = state.queue.splice(0, state.queue.length);
    queued.forEach(function (item) {
      sendMessage(item.conversationId, item.body, item.clientMsgId, item.element);
    });
  }

  /* ------------------------------------------------------------------
     Reconnect catch-up
     ------------------------------------------------------------------ */
  function resync() {
    if (!state.conversationId) return;
    var after = state.lastSeq[state.conversationId] || 0;
    state.socket.emit("sync:request", {
      conversation_id: state.conversationId,
      after_seq: after
    }, function (response) {
      if (!response || !response.ok || !response.messages) return;
      response.messages.forEach(function (message) {
        addMessage(message, { silent: true });
      });
      renderThread();
      if (state.conversationId) {
        state.socket.emit("message:read", {
          conversation_id: state.conversationId,
          up_to_seq: state.lastSeq[state.conversationId] || 0
        });
      }
    });
  }

  /* ------------------------------------------------------------------
     Messages
     ------------------------------------------------------------------ */
  function messageList(conversationId) {
    if (!state.messages[conversationId]) state.messages[conversationId] = [];
    return state.messages[conversationId];
  }

  function addMessage(message, opts) {
    var options = opts || {};
    var list = messageList(message.conversation_id);
    // Idempotent: never render the same seq twice.
    for (var i = 0; i < list.length; i++) {
      if (list[i].seq === message.seq) return false;
    }
    list.push(message);
    list.sort(function (a, b) { return a.seq - b.seq; });
    state.lastSeq[message.conversation_id] = Math.max(
      state.lastSeq[message.conversation_id] || 0, message.seq
    );
    if (!options.silent) emitLocal("message", message);
    return true;
  }

  function sendMessage(conversationId, body, clientMsgId, element) {
    clientMsgId = clientMsgId || uuid();
    var trimmed = String(body || "").trim();
    if (!trimmed) return;

    if (!state.connected) {
      state.queue.push({
        conversationId: conversationId,
        body: trimmed,
        clientMsgId: clientMsgId,
        element: element
      });
      setStatus(element, "queued", "Queued — will send when you're back");
      return;
    }

    withLatency(function () {
      if (shouldDrop()) {
        // Simulated packet loss: the frame never leaves the browser, so the
        // sender never receives an ack. The optimistic bubble becomes a
        // retryable failure – exactly what happens on a lossy network.
        window.setTimeout(function () {
          setStatus(element, "failed", "No response from server");
          showRetry(element, conversationId, trimmed, clientMsgId);
        }, 2500);
        return;
      }

      state.socket.emit("message:send", {
        conversation_id: conversationId,
        client_msg_id: clientMsgId,
        body: trimmed
      }, function (response) {
        if (!response || !response.ok) {
          var code = (response && response.code) || "SEND_FAILED";
          var message = (response && response.message) || "Could not send the message.";
          setStatus(element, "failed", message);
          showRetry(element, conversationId, trimmed, clientMsgId);
          return;
        }
        var stored = response.message;
        state.pending[clientMsgId] = { conversationId: conversationId, body: trimmed, el: element };
        addMessage(stored, { silent: true });
        if (element) {
          element.classList.remove("bubble--pending");
          setStatus(element, "sent", "Sent");
          element.setAttribute("data-seq", String(stored.seq));
        }
        delete state.pending[clientMsgId];
        renderThread();
      });
    });
  }

  function handleIncoming(payload) {
    if (!payload || !payload.message) return;
    if (shouldDrop()) {
      // Simulated packet loss on the inbound direction. The message is not
      // lost for good: the next `sync:request` (after a reconnect) brings it
      // back by sequence number.
      return;
    }
    var message = payload.message;
    var isNew = addMessage(message);
    if (!isNew) return;

    if (message.conversation_id === state.conversationId) {
      renderThread();
      // Acknowledge receipt so the sender shows a "delivered" tick.
      if (state.socket) {
        state.socket.emit("message:read", {
          conversation_id: message.conversation_id,
          up_to_seq: message.seq
        });
      }
    } else {
      renderConversations();
    }
    loadConversations();
  }

  function markRead(upToSeq) {
    var list = messageList(state.conversationId);
    list.forEach(function (message) {
      if (message.seq <= upToSeq && !message.read_at) {
        message.read_at = new Date().toISOString();
      }
    });
    renderThread();
  }

  function showRetry(element, conversationId, body, clientMsgId) {
    if (!element) return;
    var meta = element.querySelector(".bubble__meta");
    if (!meta) return;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "bubble__retry";
    button.textContent = "Retry";
    button.addEventListener("click", function () {
      element.classList.remove("bubble--failed");
      element.classList.add("bubble--pending");
      sendMessage(conversationId, body, clientMsgId, element);
    });
    meta.appendChild(button);
  }

  /* ------------------------------------------------------------------
     Rendering helpers
     ------------------------------------------------------------------ */
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function formatTime(iso) {
    if (!iso) return "";
    var d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function dayLabel(iso) {
    if (!iso) return "";
    var d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
    var today = new Date();
    var yesterday = new Date();
    yesterday.setDate(today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
    return d.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
  }

  function statusIcon(status) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    if (status === "read") {
      path.setAttribute("d", "M18 6 7 17l-5-5M22 6l-9.5 9.5");
    } else if (status === "failed") {
      path.setAttribute("d", "M18 6 6 18M6 6l12 12");
    } else if (status === "queued" || status === "sending") {
      path.setAttribute("d", "M12 6v6l4 2");
    } else {
      path.setAttribute("d", "M20 6 9 17l-5-5");
    }
    svg.appendChild(path);
    return svg;
  }

  function setStatus(element, status, label) {
    if (!element) return;
    var meta = element.querySelector(".bubble__meta");
    if (!meta) return;
    var old = meta.querySelector("[data-status]");
    if (old) old.remove();
    var span = el("span", "bubble__status");
    span.setAttribute("data-status", status);
    span.appendChild(statusIcon(status));
    var text = el("span", null, label);
    span.appendChild(text);
    meta.appendChild(span);
  }

  function renderConversations() {
    var list = document.getElementById("conversationList");
    if (!list) return;
    list.textContent = "";

    if (!state.conversations.length) {
      var empty = el("div", "empty-state");
      var icon = el("div", "empty-state__icon");
      icon.innerHTML = '<svg aria-hidden="true"><use href="/static/icons/sprite.svg#icon-message-circle"></use></svg>';
      empty.appendChild(icon);
      empty.appendChild(el("h3", null, "No conversations yet"));
      empty.appendChild(el("p", null, "Accept a mentorship request to start chatting."));
      list.appendChild(empty);
      return;
    }

    state.conversations.forEach(function (conversation) {
      var button = el("button", "conversation-item");
      button.type = "button";
      if (conversation.id === state.conversationId) button.classList.add("is-active");

      var avatar = el("span", "avatar", (conversation.other_name || "?").slice(0, 2).toUpperCase());
      var online = el("span", "avatar__dot");
      if (conversation.online) online.classList.add("avatar__dot--online");
      var wrap = el("span", "avatar-wrap");
      wrap.appendChild(avatar);
      wrap.appendChild(online);
      button.appendChild(wrap);

      var body = el("span", "conversation-item__body");
      var top = el("span", "conversation-item__top");
      top.appendChild(el("span", "conversation-item__name", conversation.other_name || "Unknown"));
      top.appendChild(el("span", "conversation-item__time",
        conversation.last_message_at ? formatTime(conversation.last_message_at) : ""));
      body.appendChild(top);

      var preview = conversation.last_message
        ? (conversation.last_message.body || "")
        : "No messages yet";
      body.appendChild(el("span", "conversation-item__preview", preview));
      button.appendChild(body);

      if (conversation.unread) {
        button.appendChild(el("span", "conversation-item__badge", String(conversation.unread)));
      }

      button.addEventListener("click", function () {
        window.location.href = "/messages/" + conversation.id;
      });

      list.appendChild(button);
    });
  }

  function renderThread() {
    var thread = document.getElementById("messageThread");
    if (!thread || !state.conversationId) return;

    var atBottom = isNearBottom();
    var previousHeight = thread.scrollHeight;

    thread.textContent = "";
    var list = messageList(state.conversationId);
    var lastDay = null;
    var lastSender = null;

    list.forEach(function (message) {
      var day = dayLabel(message.created_at);
      if (day !== lastDay) {
        thread.appendChild(el("div", "day-separator", day));
        lastDay = day;
        lastSender = null;
      }

      var mine = message.sender_id === state.currentUserId;
      var bubble = el("div", "bubble " + (mine ? "bubble--mine" : "bubble--theirs"));
      if (lastSender === message.sender_id) {
        bubble.style.marginTop = "2px";
        bubble.style.borderTopLeftRadius = "4px";
        bubble.style.borderTopRightRadius = "4px";
      }

      // Message bodies are user content: textContent only.
      bubble.appendChild(el("span", "bubble__body", message.body));

      var meta = el("div", "bubble__meta");
      meta.appendChild(el("span", null, formatTime(message.created_at)));
      bubble.appendChild(meta);

      if (mine) {
        if (message.read_at) setStatus(bubble, "read", "Read");
        else if (message.delivered_at) setStatus(bubble, "delivered", "Delivered");
        else setStatus(bubble, "sent", "Sent");
      }

      thread.appendChild(bubble);
      lastSender = message.sender_id;
    });

    if (atBottom) {
      thread.scrollTop = thread.scrollHeight;
    } else {
      thread.scrollTop = thread.scrollHeight - previousHeight;
    }
  }

  function renderTyping(conversationId) {
    var indicator = document.getElementById("typingIndicator");
    if (!indicator) return;
    if (conversationId !== state.conversationId) {
      indicator.hidden = true;
      return;
    }
    var typing = state.typingUsers[conversationId];
    indicator.hidden = !typing;
  }

  function isNearBottom() {
    var thread = document.getElementById("messageThread");
    if (!thread) return true;
    return thread.scrollHeight - thread.scrollTop - thread.clientHeight < 120;
  }

  /* ------------------------------------------------------------------
     Data loading
     ------------------------------------------------------------------ */
  function loadConversations() {
    if (!state.socket) return;
    return window.CT.fetchJSON("/api/conversations")
      .then(function (data) {
        state.conversations = data.conversations || [];
        renderConversations();
      })
      .catch(function () { /* keep the previous list */ });
  }

  function loadMessages() {
    if (!state.conversationId) return Promise.resolve();
    return window.CT.fetchJSON(
      "/api/conversations/" + state.conversationId + "/messages?limit=30"
    )
      .then(function (data) {
        state.messages[state.conversationId] = data.messages || [];
        var max = 0;
        state.messages[state.conversationId].forEach(function (m) { max = Math.max(max, m.seq); });
        state.lastSeq[state.conversationId] = max;
        renderThread();
      })
      .catch(function (error) {
        if (window.CT) window.CT.toast(error.message || "Could not load messages.", "error");
      });
  }

  function loadOlderMessages() {
    if (!state.conversationId) return;
    var list = messageList(state.conversationId);
    if (!list.length) return;
    var oldest = list[0].seq;
    if (state.loadingOlder) return;
    state.loadingOlder = true;

    window.CT.fetchJSON(
      "/api/conversations/" + state.conversationId + "/messages?before_seq=" + oldest + "&limit=30"
    )
      .then(function (data) {
        var older = data.messages || [];
        if (!older.length) return;
        state.messages[state.conversationId] = older.concat(list);
        renderThread();
      })
      .catch(function () { /* ignore */ })
      .finally(function () { state.loadingOlder = false; });
  }

  /* ------------------------------------------------------------------
     UI wiring
     ------------------------------------------------------------------ */
  function initUI() {
    var root = document.getElementById("chatRoot");
    if (!root) return;

    var input = document.getElementById("messageInput");
    var counter = document.getElementById("charCounter");
    var sendBtn = document.getElementById("sendBtn");
    var thread = document.getElementById("messageThread");
    var scrollLatest = document.getElementById("scrollLatest");
    var chat = document.getElementById("chatLayout");
    var back = document.getElementById("chatBack");

    function updateCounter() {
      if (!counter) return;
      var length = input.value.length;
      counter.textContent = length + " / " + MAX_CHARS;
      counter.classList.toggle("over", length > MAX_CHARS);
    }

    function submit() {
      var body = input.value.trim();
      if (!body) return;
      if (body.length > MAX_CHARS) {
        if (window.CT) window.CT.toast("Message is too long.", "warning");
        return;
      }
      if (!state.conversationId) return;

      // Optimistic bubble.
      var bubble = el("div", "bubble bubble--mine bubble--pending");
      bubble.appendChild(el("span", "bubble__body", body));
      var meta = el("div", "bubble__meta");
      meta.appendChild(el("span", null, formatTime(new Date().toISOString())));
      bubble.appendChild(meta);
      setStatus(bubble, "sending", "Sending");
      thread.appendChild(bubble);
      thread.scrollTop = thread.scrollHeight;

      input.value = "";
      updateCounter();
      sendMessage(state.conversationId, body, null, bubble);
    }

    if (input) {
      input.addEventListener("input", function () {
        updateCounter();
        if (state.socket && state.connected && state.conversationId) {
          state.socket.emit("typing:start", { conversation_id: state.conversationId });
        }
      });
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          submit();
        }
      });
      input.addEventListener("blur", function () {
        if (state.socket && state.connected && state.conversationId) {
          state.socket.emit("typing:stop", { conversation_id: state.conversationId });
        }
      });
    }

    if (sendBtn) sendBtn.addEventListener("click", submit);

    if (thread) {
      thread.addEventListener("scroll", function () {
        if (thread.scrollTop < 60) loadOlderMessages();
        if (scrollLatest) {
          scrollLatest.classList.toggle("is-visible", !isNearBottom());
        }
      });
    }

    if (scrollLatest) {
      scrollLatest.addEventListener("click", function () {
        thread.scrollTop = thread.scrollHeight;
        scrollLatest.classList.remove("is-visible");
      });
    }

    if (back && chat) {
      back.addEventListener("click", function () {
        chat.classList.remove("chat--thread-open");
      });
    }

    updateCounter();
  }

  /* ------------------------------------------------------------------
     Public API
     ------------------------------------------------------------------ */
  window.CTChat = {
    state: state,
    connect: connect,
    on: on,
    emitLocal: emitLocal,
    sendMessage: sendMessage,
    loadConversations: loadConversations,
    loadMessages: loadMessages,
    resync: resync,
    setSimulation: setSimulation,
    uuid: uuid
  };

  document.addEventListener("DOMContentLoaded", function () {
    var root = document.getElementById("chatRoot");
    if (root) {
      state.conversationId = parseInt(root.getAttribute("data-conversation-id"), 10) || null;
      state.currentUserId = parseInt(root.getAttribute("data-current-user-id"), 10) || null;
    }

    initUI();
    connect();

    if (state.conversationId) {
      loadMessages();
      loadConversations();
      if (root) root.classList.add("chat--thread-open");
    } else {
      loadConversations();
    }
  });
})();
