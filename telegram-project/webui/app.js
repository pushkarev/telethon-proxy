let selectedPeerId = null;
let selectedWhatsAppJid = null;
let activeSection = "configuration";
let activeTelegramPane = "chats";
let telegramAuthState = null;
let whatsappAuthState = null;

const bridge = window.telethonProxy || null;
const FALLBACK_API_BASE = "http://127.0.0.1:8788";
let apiBasePromise = null;

const DEFAULT_TELEGRAM_AUTH = {
  keychain_backend: "macOS Keychain",
  has_api_credentials: false,
  has_session: false,
  phone: "",
  saved_phone: null,
  next_step: "credentials",
  pending_phone: null,
  last_error: null,
};

const DEFAULT_WHATSAPP_AUTH = {
  available: true,
  connected: false,
  has_session: false,
  qr_available: false,
  qr_raw: null,
  qr_ascii: null,
  qr_svg: null,
  cloud_label_name: "Cloud",
  cloud_label_found: false,
  chats: [],
  last_error: null,
  connection: "idle",
  me: null,
  auth_dir: "",
};

function el(id) { return document.getElementById(id); }

function formatApiError(url, status, payload) {
  if (status === 404 && String(url).startsWith("/api/telegram/auth")) {
    return "Telegram Settings needs the newer background service. Restart the app or local proxy service and try again.";
  }
  if (status === 404 && String(url).startsWith("/api/whatsapp/auth")) {
    return "WhatsApp support needs the newer background service. Restart the app or local proxy service and try again.";
  }
  return payload?.error || `Request failed with status ${status}`;
}

function fmtDate(value) {
  if (!value) return "unknown";
  return new Date(value).toLocaleString();
}

function fmtTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function fmtDay(value) {
  if (!value) return "";
  return new Date(value).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function setNotice(message, tone = "error") {
  const node = el("notice");
  if (!message) {
    node.style.display = "none";
    node.textContent = "";
    node.dataset.tone = "";
    return;
  }
  node.style.display = "block";
  node.dataset.tone = tone;
  node.textContent = message;
}

let mcpCopyBubbleTimer = null;

function showMcpCopyBubble(message) {
  const bubble = el("mcpCopyBubble");
  if (!bubble) return;
  bubble.textContent = message;
  bubble.classList.add("visible");
  if (mcpCopyBubbleTimer) {
    clearTimeout(mcpCopyBubbleTimer);
  }
  mcpCopyBubbleTimer = setTimeout(() => {
    bubble.classList.remove("visible");
  }, 1800);
}

async function copyText(value) {
  if (bridge?.copyText) {
    return bridge.copyText(value);
  }
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const input = document.createElement("textarea");
  input.value = value;
  input.setAttribute("readonly", "");
  input.style.position = "absolute";
  input.style.left = "-9999px";
  document.body.appendChild(input);
  input.select();
  document.execCommand("copy");
  input.remove();
}

function setActiveSection(section) {
  activeSection = section;
  document.querySelectorAll(".folder-button").forEach((node) => {
    node.classList.toggle("active", node.dataset.section === section);
  });
  document.querySelectorAll(".section-panel").forEach((node) => {
    node.classList.toggle("active", node.dataset.sectionPanel === section);
  });
}

function setActiveTelegramPane(pane) {
  activeTelegramPane = pane;
  document.querySelectorAll(".telegram-pane-button").forEach((node) => {
    node.classList.toggle("active", node.dataset.telegramPane === pane);
  });
  document.querySelectorAll(".telegram-pane").forEach((node) => {
    node.classList.toggle("active", node.dataset.telegramPanePanel === pane);
  });
}

async function postJson(url, payload) {
  const apiBase = await getApiBase();
  if (bridge?.apiPost) {
    return bridge.apiPost(url, payload);
  }
  const response = await fetch(new URL(url, `${apiBase}/`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(formatApiError(url, response.status, data));
  }
  return data;
}

async function getJson(url) {
  const apiBase = await getApiBase();
  if (bridge?.apiGet) {
    return bridge.apiGet(url);
  }
  const response = await fetch(new URL(url, `${apiBase}/`));
  const data = await response.json();
  if (!response.ok) {
    throw new Error(formatApiError(url, response.status, data));
  }
  return data;
}

async function getApiBase() {
  if (!apiBasePromise) {
    apiBasePromise = (async () => {
      if (bridge?.backgroundApiBase) {
        try {
          return await bridge.backgroundApiBase();
        } catch {
          return FALLBACK_API_BASE;
        }
      }
      return FALLBACK_API_BASE;
    })();
  }
  return apiBasePromise;
}

async function loadOverview() {
  const raw = await getJson("/api/overview");
  const data = {
    ...raw,
    telegram_auth: {
      ...DEFAULT_TELEGRAM_AUTH,
      ...(raw.telegram_auth || {}),
    },
    whatsapp: {
      ...DEFAULT_WHATSAPP_AUTH,
      ...(raw.whatsapp || {}),
    },
  };

  renderOverview(data);
  renderChats(data.chats);
  renderApis(data.apis);
  renderTelegramAuth(data.telegram_auth);
  renderWhatsAppAuth(data.whatsapp);

  if (!selectedPeerId && data.chats.length) {
    selectedPeerId = data.chats[0].peer_id;
    await loadTelegramChat(selectedPeerId);
  } else if (selectedPeerId) {
    await loadTelegramChat(selectedPeerId);
  }

  const whatsappChats = Array.isArray(data.whatsapp.chats) ? data.whatsapp.chats : [];
  if (!selectedWhatsAppJid && whatsappChats.length) {
    selectedWhatsAppJid = whatsappChats[0].jid;
    await loadWhatsAppChat(selectedWhatsAppJid);
  } else if (selectedWhatsAppJid) {
    await loadWhatsAppChat(selectedWhatsAppJid);
  }
}

function renderOverview(data) {
  const telegramAuth = {
    ...DEFAULT_TELEGRAM_AUTH,
    ...(data.telegram_auth || {}),
  };
  const whatsappAuth = {
    ...DEFAULT_WHATSAPP_AUTH,
    ...(data.whatsapp || {}),
  };

  el("clientCount").textContent = data.clients.length;
  el("chatCount").textContent = data.chats.length;
  el("dashboardAddress").textContent = `${data.config.dashboard_host}:${data.config.dashboard_port}`;
  el("folderBadge").textContent = data.config.cloud_folder_name;
  el("telegramBadge").textContent = telegramAuth.has_session ? "Authorized" : "Needs login";
  el("whatsappBadge").textContent = whatsappAuth.connected ? "Connected" : (whatsappAuth.has_session ? "Reconnect needed" : "Needs QR");

  el("configGrid").innerHTML = [
    ["Telegram Cloud folder", data.config.cloud_folder_name],
    ["WhatsApp Cloud label", data.config.whatsapp_cloud_label_name || "Cloud"],
    ["MTProto endpoint", `${data.config.downstream_host}:${data.config.mtproto_port}`],
    ["Background API", `${data.config.dashboard_host}:${data.config.dashboard_port}`],
    ["Issued sessions", data.config.issued_client_count],
    ["Reconnect backoff", `${data.config.upstream_reconnect_min_delay}s -> ${data.config.upstream_reconnect_max_delay}s`],
    ["Allow member listing", data.config.allow_member_listing ? "yes" : "no"],
    ["Proxy session label", data.config.downstream_session_label],
  ].map(([key, value]) => `<div>${esc(key)}</div><div>${esc(value)}</div>`).join("");

  el("upstreamGrid").innerHTML = [
    ["Name", data.upstream.name || "unknown"],
    ["Phone", data.upstream.phone || "unknown"],
    ["Username", data.upstream.username ? "@" + data.upstream.username : "none"],
  ].map(([key, value]) => `<div>${esc(key)}</div><div>${esc(value)}</div>`).join("");

  el("credentialList").innerHTML = data.downstream_credentials.length
    ? data.downstream_credentials.map((cred) => `
        <div class="credential-card">
          <div class="row">
            <div class="title">${esc(cred.label)}</div>
            <span class="pill">${cred.phone ? "authorized" : "issued"}</span>
          </div>
          <div class="kv">
            <div>Host</div><div>${esc(cred.host || data.config.downstream_host)}</div>
            <div>Port</div><div>${esc(cred.port || data.config.mtproto_port)}</div>
            <div>API ID</div><div>${esc(data.config.downstream_api_id)}</div>
            <div>API Hash</div><div>${esc(data.config.downstream_api_hash)}</div>
            <div>Proxy phone</div><div>${esc(data.config.downstream_login_phone)}</div>
            <div>Proxy code</div><div>${esc(data.config.downstream_login_code)}</div>
          </div>
          <div class="meta">Created ${esc(fmtDate(cred.created_at))}${cred.phone ? `<br />Bound phone ${esc(cred.phone)}` : ""}</div>
          ${cred.session_string
            ? `<textarea class="credential-session" readonly>${esc(cred.session_string)}</textarea>`
            : `<div class="empty">Session string was not retained for this issued client.</div>`}
        </div>
      `).join("")
    : '<div class="empty">No downstream client credentials have been issued yet.</div>';

  el("clientList").innerHTML = data.clients.length
    ? data.clients.map((client) => `
        <div class="client">
          <div class="row">
            <div class="title">${esc(client.label)}</div>
            <span class="pill">${client.authorized ? "authorized" : "pending"}</span>
          </div>
          <div class="meta">
            ${esc(client.remote_addr)}<br />
            connected ${esc(fmtDate(client.connected_at))}<br />
            ${client.phone ? `phone ${esc(client.phone)}<br />` : ""}
            key <span class="mono">${esc(client.key_id)}</span>
          </div>
        </div>
      `).join("")
    : '<div class="empty">No clients are connected right now.</div>';

  el("mcpGrid").innerHTML = [
    ["Endpoint", `http://${data.mcp.host}:${data.mcp.port}${data.mcp.path}`],
    ["Transport", data.mcp.transport],
    ["Auth", data.mcp.auth],
    ["Allowed origin", data.mcp.allowed_origin],
  ].map(([key, value]) => `<div>${esc(key)}</div><div>${esc(value)}</div>`).join("");

  el("mcpCard").innerHTML = `
    <div class="row">
      <div class="title">Bearer token</div>
      <span class="pill">local MCP</span>
    </div>
    <div class="meta">
      The bearer token stays hidden in the app. Use copy when you need it, or revoke it to invalidate the current token and copy a fresh one.<br />
      Example endpoint: <span class="mono">${esc(`http://${data.mcp.host}:${data.mcp.port}${data.mcp.path}`)}</span><br />
      Suggested tools: <span class="mono">telegram.list_chats</span>, <span class="mono">telegram.get_messages</span>, <span class="mono">whatsapp.list_chats</span>, <span class="mono">whatsapp.get_messages</span>, <span class="mono">whatsapp.send_message</span><br />
      Resources: <span class="mono">telegram://config</span>, <span class="mono">telegram://chat/&lt;peer_id&gt;</span>, <span class="mono">whatsapp://config</span>, <span class="mono">whatsapp://chat/&lt;jid&gt;</span><br />
      Subscriptions: open SSE with <span class="mono">Mcp-Session-Id</span> and subscribe to <span class="mono">telegram://updates</span> or <span class="mono">telegram://chat/&lt;peer_id&gt;</span>
    </div>
    <div class="auth-actions">
      <button type="button" class="secondary-button" id="copyMcpTokenButton">Copy bearer token</button>
      <button type="button" class="danger-button" id="rotateMcpTokenButton"${data.mcp.token_env_managed ? " disabled" : ""}>Revoke and copy new token</button>
      <span class="copy-bubble" id="mcpCopyBubble" aria-live="polite"></span>
    </div>
    ${data.mcp.token_env_managed ? `<div class="meta">This token is managed by <span class="mono">TP_MCP_TOKEN</span>, so rotation from the UI is disabled.</div>` : ""}
  `;

  const copyMcpTokenButton = el("copyMcpTokenButton");
  if (copyMcpTokenButton) {
    copyMcpTokenButton.addEventListener("click", async () => {
      const result = await getJson("/api/mcp/token");
      await copyText(result.token);
      showMcpCopyBubble("Token copied");
    });
  }

  const rotateMcpTokenButton = el("rotateMcpTokenButton");
  if (rotateMcpTokenButton && !data.mcp.token_env_managed) {
    rotateMcpTokenButton.addEventListener("click", async () => {
      const result = await postJson("/api/mcp/token/rotate", {});
      await copyText(result.token);
      setNotice(result.message || "MCP token rotated.", "success");
      await loadOverview();
      showMcpCopyBubble("New token copied");
    });
  }

  if (data.error) {
    setNotice(data.error, "error");
  } else if (!telegramAuth.last_error && !whatsappAuth.last_error) {
    setNotice("");
  }
}

function renderChats(chats) {
  if (!chats.length) {
    selectedPeerId = null;
  }

  el("chatList").innerHTML = chats.length
    ? chats.map((chat) => `
        <div class="chat ${selectedPeerId === chat.peer_id ? "active" : ""}" data-peer="${chat.peer_id}">
          <div class="row">
            <div class="title">${esc(chat.title)}</div>
            <span class="pill">${esc(chat.kind)}</span>
          </div>
          <div class="meta">${chat.username ? "@" + esc(chat.username) + "<br />" : ""}peer ${esc(chat.peer_id)}</div>
        </div>
      `).join("")
    : '<div class="empty">No Cloud chats are currently visible.</div>';

  document.querySelectorAll("#chatList .chat").forEach((node) => {
    node.addEventListener("click", async () => {
      selectedPeerId = Number(node.dataset.peer);
      renderChats(chats);
      await loadTelegramChat(selectedPeerId);
    });
  });
}

function renderWhatsAppChats(chats) {
  if (!chats.length) {
    selectedWhatsAppJid = null;
  }

  el("whatsappChatList").innerHTML = chats.length
    ? chats.map((chat) => `
        <div class="chat ${selectedWhatsAppJid === chat.jid ? "active" : ""}" data-jid="${esc(chat.jid)}">
          <div class="row">
            <div class="title">${esc(chat.title)}</div>
            <span class="pill">${esc(chat.kind)}</span>
          </div>
          <div class="meta">
            ${esc(chat.jid)}<br />
            ${chat.labels?.length ? `labels ${esc(chat.labels.join(", "))}<br />` : ""}
            ${chat.last_message_at ? `last ${esc(fmtDate(chat.last_message_at))}` : "no recent messages"}
          </div>
        </div>
      `).join("")
    : '<div class="empty">No WhatsApp chats currently carry the Cloud label.</div>';

  document.querySelectorAll("#whatsappChatList .chat").forEach((node) => {
    node.addEventListener("click", async () => {
      selectedWhatsAppJid = node.dataset.jid;
      renderWhatsAppChats(chats);
      await loadWhatsAppChat(selectedWhatsAppJid);
    });
  });
}

function renderMessageTimeline(messages, {
  headingId,
  kindId,
  metaId,
  listId,
  fallbackHeading,
  fallbackKind,
  fallbackMeta,
  title,
  kind,
  meta,
}) {
  el(headingId).textContent = title || fallbackHeading;
  el(kindId).textContent = kind || fallbackKind;
  el(metaId).innerHTML = meta || fallbackMeta;

  if (!messages.length) {
    el(listId).innerHTML = '<div class="empty">No recent messages were returned for this chat.</div>';
    return;
  }

  let lastDay = null;
  const html = [];
  for (const message of messages.slice().reverse()) {
    const day = fmtDay(message.date);
    if (day && day !== lastDay) {
      html.push(`<div class="day-stamp">${esc(day)}</div>`);
      lastDay = day;
    }
    html.push(`
      <div class="message-row ${message.out || message.from_me ? "outgoing" : "incoming"}">
        <div class="message-bubble">
          <div class="message-text">${esc(message.text || "[non-text message]")}</div>
          <div class="message-meta">
            ${message.media || message.kind ? `<span class="message-chip">${esc(message.media || message.kind)}</span>` : ""}
            <span>${esc(fmtTime(message.date))}</span>
          </div>
        </div>
      </div>
    `);
  }
  el(listId).innerHTML = html.join("");
  el(listId).scrollTop = el(listId).scrollHeight;
}

async function loadTelegramChat(peerId) {
  if (!peerId) {
    return;
  }
  const data = await getJson(`/api/chat?peer_id=${encodeURIComponent(peerId)}`);
  renderMessageTimeline(data.messages || [], {
    headingId: "messageHeading",
    kindId: "chatKindPill",
    metaId: "chatScreenMeta",
    listId: "messageList",
    fallbackHeading: "Messages",
    fallbackKind: "chat",
    fallbackMeta: "Pick a Cloud chat to inspect recent history.",
    title: data.chat ? data.chat.title : null,
    kind: data.chat ? data.chat.kind : null,
    meta: data.chat
      ? `${data.chat.username ? "@" + esc(data.chat.username) + " · " : ""}peer ${esc(data.chat.peer_id)}`
      : null,
  });
}

async function loadWhatsAppChat(jid) {
  if (!jid) {
    return;
  }
  const data = await getJson(`/api/whatsapp/chat?jid=${encodeURIComponent(jid)}`);
  if (data.error) {
    renderMessageTimeline([], {
      headingId: "whatsappMessageHeading",
      kindId: "whatsappChatKindPill",
      metaId: "whatsappChatScreenMeta",
      listId: "whatsappMessageList",
      fallbackHeading: "Messages",
      fallbackKind: "chat",
      fallbackMeta: esc(data.error),
      title: null,
      kind: null,
      meta: null,
    });
    return;
  }
  renderMessageTimeline(data.messages || [], {
    headingId: "whatsappMessageHeading",
    kindId: "whatsappChatKindPill",
    metaId: "whatsappChatScreenMeta",
    listId: "whatsappMessageList",
    fallbackHeading: "Messages",
    fallbackKind: "chat",
      fallbackMeta: "Scan the QR and label chats with Cloud to inspect recent history.",
    title: data.chat ? data.chat.title : null,
    kind: data.chat ? data.chat.kind : null,
    meta: data.chat
      ? `${esc(data.chat.jid)}${data.chat.labels?.length ? ` · labels ${esc(data.chat.labels.join(", "))}` : ""}`
      : null,
  });
}

function renderApis(apis) {
  el("forwardedApis").innerHTML = apis.forwarded.map((name) => `<div class="api"><code>${esc(name)}</code></div>`).join("");
  el("localApis").innerHTML = apis.proxy_local.map((name) => `<div class="api"><code>${esc(name)}</code></div>`).join("");
}

function renderTelegramAuth(state) {
  telegramAuthState = {
    ...DEFAULT_TELEGRAM_AUTH,
    ...(state || {}),
  };

  el("telegramPhone").value = telegramAuthState.pending_phone || telegramAuthState.phone || el("telegramPhone").value || "";
  el("telegramAuthStatus").innerHTML = `
    <div class="row">
      <div class="title">Authentication status</div>
      <span class="pill">${esc(telegramAuthState.next_step)}</span>
    </div>
    <div class="kv">
      <div>Storage</div><div>${esc(telegramAuthState.keychain_backend)}</div>
      <div>API keys</div><div>${telegramAuthState.has_api_credentials ? "saved" : "missing"}</div>
      <div>Session</div><div>${telegramAuthState.has_session ? "saved" : "not authorized yet"}</div>
      <div>Phone</div><div>${esc(telegramAuthState.pending_phone || telegramAuthState.phone || "not set")}</div>
    </div>
    ${telegramAuthState.saved_phone
      ? `<div class="saved-session-row">
          <div>
            <div class="title-small">Saved session</div>
            <div class="meta">${esc(telegramAuthState.saved_phone)}</div>
          </div>
          <button type="button" class="danger-button compact-button" id="deleteSavedSessionButton">Delete session</button>
        </div>`
      : ""}
    ${telegramAuthState.account ? `<div class="meta">Authorized as ${esc(telegramAuthState.account.name)}${telegramAuthState.account.username ? ` (@${esc(telegramAuthState.account.username)})` : ""}</div>` : ""}
    ${telegramAuthState.has_api_credentials && !telegramAuthState.has_session ? `<div class="meta">Saved API keys are ready. Request a new login code to sign back in.</div>` : ""}
    ${telegramAuthState.last_error ? `<div class="inline-notice">${esc(telegramAuthState.last_error)}</div>` : ""}
  `;

  el("telegramCodeForm").classList.toggle("visible", telegramAuthState.next_step === "code");
  el("telegramPasswordForm").classList.toggle("visible", telegramAuthState.next_step === "password");

  const deleteButton = document.getElementById("deleteSavedSessionButton");
  if (deleteButton) {
    deleteButton.addEventListener("click", () => {
      clearTelegramSession().catch((error) => setNotice(error.message || String(error), "error"));
    });
  }
}

function renderWhatsAppAuth(state) {
  whatsappAuthState = {
    ...DEFAULT_WHATSAPP_AUTH,
    ...(state || {}),
  };

  const chats = Array.isArray(whatsappAuthState.chats) ? whatsappAuthState.chats : [];
  el("whatsappBadge").textContent = whatsappAuthState.connected ? "Connected" : (whatsappAuthState.has_session ? "Reconnect needed" : "Needs QR");
  el("whatsappAuthStatus").innerHTML = `
    <div class="row">
      <div class="title">Bridge status</div>
      <span class="pill">${esc(whatsappAuthState.connection || "idle")}</span>
    </div>
    <div class="kv">
      <div>Bridge</div><div>${whatsappAuthState.available ? "available" : "unavailable"}</div>
      <div>Session</div><div>${whatsappAuthState.has_session ? "saved" : "not paired yet"}</div>
      <div>Cloud label</div><div>${esc(whatsappAuthState.cloud_label_name || "Cloud")}${whatsappAuthState.cloud_label_found ? "" : " (not seen yet)"}</div>
      <div>Visible chats</div><div>${esc(chats.length)}</div>
      <div>Auth storage</div><div>${esc(whatsappAuthState.auth_dir || "local files")}</div>
    </div>
    ${whatsappAuthState.me ? `<div class="meta">Linked as ${esc(whatsappAuthState.me.name || whatsappAuthState.me.id || "WhatsApp account")}</div>` : ""}
    ${whatsappAuthState.last_error ? `<div class="inline-notice">${esc(whatsappAuthState.last_error)}</div>` : ""}
  `;

  el("whatsappQrCard").innerHTML = whatsappAuthState.qr_svg
    ? `
        <div class="whatsapp-qr">
          ${whatsappAuthState.qr_svg}
          <div class="whatsapp-qr-meta">
            Scan this QR from WhatsApp on your phone.<br />
            If it expires, use <span class="mono">Refresh QR</span>.
          </div>
        </div>
      `
    : `
        <div class="whatsapp-qr">
          <div class="whatsapp-qr-meta">
            ${whatsappAuthState.connected
              ? "WhatsApp is already connected."
              : "Waiting for the next QR from the WhatsApp bridge..."}
          </div>
        </div>
      `;

  renderWhatsAppChats(chats);
  if (!selectedWhatsAppJid) {
    el("whatsappMessageHeading").textContent = "Messages";
    el("whatsappChatKindPill").textContent = "chat";
    el("whatsappChatScreenMeta").textContent = "Scan the QR and label chats with Cloud to inspect recent history.";
    el("whatsappMessageList").innerHTML = '<div class="empty">No WhatsApp chat is selected yet.</div>';
  }
}

async function refreshTelegramAuth() {
  renderTelegramAuth(await getJson("/api/telegram/auth"));
}

async function refreshWhatsAppAuth() {
  renderWhatsAppAuth(await getJson("/api/whatsapp/auth"));
}

async function saveTelegramCredentials(event) {
  event.preventDefault();
  const result = await saveTelegramCredentialsFromForm();
  renderTelegramAuth(result);
  setNotice("Telegram API keys were saved to the Keychain.", "success");
}

async function saveTelegramCredentialsFromForm() {
  return postJson("/api/telegram/auth/save", {
    api_id: el("telegramApiId").value.trim(),
    api_hash: el("telegramApiHash").value.trim(),
    phone: el("telegramPhone").value.trim(),
  });
}

async function requestTelegramCode() {
  const apiId = el("telegramApiId").value.trim();
  const apiHash = el("telegramApiHash").value.trim();
  if (apiId || apiHash) {
    await saveTelegramCredentialsFromForm();
  }
  const result = await postJson("/api/telegram/auth/request-code", {
    phone: el("telegramPhone").value.trim(),
  });
  renderTelegramAuth(result);
  setActiveSection("telegram");
  setActiveTelegramPane("settings");
  setNotice("Telegram login code requested. Enter it in the Telegram settings panel.", "success");
}

async function submitTelegramCode(event) {
  event.preventDefault();
  const result = await postJson("/api/telegram/auth/submit-code", {
    code: el("telegramCode").value.trim(),
  });
  el("telegramCode").value = "";
  renderTelegramAuth(result);
  if (result.next_step === "password") {
    setNotice("Telegram asked for the account 2FA password.", "success");
    return;
  }
  setNotice("Telegram account authorized and saved to the Keychain.", "success");
  await loadOverview();
}

async function submitTelegramPassword(event) {
  event.preventDefault();
  const result = await postJson("/api/telegram/auth/submit-password", {
    password: el("telegramPassword").value,
  });
  el("telegramPassword").value = "";
  renderTelegramAuth(result);
  setNotice("Telegram account authorized and saved to the Keychain.", "success");
  await loadOverview();
}

async function clearTelegramAuth() {
  const result = await postJson("/api/telegram/auth/clear", {});
  el("telegramApiId").value = "";
  el("telegramApiHash").value = "";
  el("telegramPhone").value = "";
  el("telegramCode").value = "";
  el("telegramPassword").value = "";
  selectedPeerId = null;
  el("chatList").innerHTML = '<div class="empty">No Cloud chats are currently visible.</div>';
  el("messageHeading").textContent = "Messages";
  el("chatKindPill").textContent = "chat";
  el("chatScreenMeta").textContent = "Pick a Cloud chat to inspect recent history.";
  el("messageList").innerHTML = '<div class="empty">Authorize Telegram in Settings to load Cloud chats.</div>';
  renderTelegramAuth(result);
  setNotice("Saved Telegram keys and session were cleared from Keychain.", "success");
  await loadOverview();
}

async function clearTelegramSession() {
  const result = await postJson("/api/telegram/auth/clear-session", {});
  el("telegramCode").value = "";
  el("telegramPassword").value = "";
  selectedPeerId = null;
  el("chatList").innerHTML = '<div class="empty">No Cloud chats are currently visible.</div>';
  el("messageHeading").textContent = "Messages";
  el("chatKindPill").textContent = "chat";
  el("chatScreenMeta").textContent = "Pick a Cloud chat to inspect recent history.";
  el("messageList").innerHTML = '<div class="empty">Authorize Telegram in Settings to load Cloud chats.</div>';
  renderTelegramAuth(result);
  setNotice("Saved Telegram session was removed. Request a new login code to sign back in.", "success");
  await loadOverview();
}

async function requestWhatsAppPairingCode(event) {
  event.preventDefault();
  const result = await postJson("/api/whatsapp/auth/request-pairing-code", {});
  renderWhatsAppAuth(result);
  setActiveSection("whatsapp");
  setNotice(result.qr_available ? "WhatsApp QR refreshed. Scan it from Linked devices." : "WhatsApp login state refreshed.", "success");
  await loadOverview();
}

async function clearWhatsAppSession() {
  const result = await postJson("/api/whatsapp/auth/logout", {});
  selectedWhatsAppJid = null;
  el("whatsappChatList").innerHTML = '<div class="empty">No WhatsApp chats currently carry the Cloud label.</div>';
  el("whatsappMessageHeading").textContent = "Messages";
  el("whatsappChatKindPill").textContent = "chat";
  el("whatsappChatScreenMeta").textContent = "Scan the QR and label chats with Cloud to inspect recent history.";
  el("whatsappMessageList").innerHTML = '<div class="empty">Refresh the QR to connect WhatsApp.</div>';
  renderWhatsAppAuth(result);
  setNotice("Saved WhatsApp session was cleared. Refresh the QR to reconnect.", "success");
  await loadOverview();
}

document.querySelectorAll(".folder-button").forEach((node) => {
  node.addEventListener("click", () => setActiveSection(node.dataset.section));
});

document.querySelectorAll(".telegram-pane-button").forEach((node) => {
  node.addEventListener("click", () => setActiveTelegramPane(node.dataset.telegramPane));
});

el("telegramCredentialForm").addEventListener("submit", (event) => {
  saveTelegramCredentials(event).catch((error) => setNotice(error.message || String(error), "error"));
});

el("requestCodeButton").addEventListener("click", () => {
  requestTelegramCode().catch((error) => setNotice(error.message || String(error), "error"));
});

el("clearKeychainButton").addEventListener("click", () => {
  clearTelegramAuth().catch((error) => setNotice(error.message || String(error), "error"));
});

el("telegramCodeForm").addEventListener("submit", (event) => {
  submitTelegramCode(event).catch((error) => setNotice(error.message || String(error), "error"));
});

el("telegramPasswordForm").addEventListener("submit", (event) => {
  submitTelegramPassword(event).catch((error) => setNotice(error.message || String(error), "error"));
});

el("whatsappPairingForm").addEventListener("submit", (event) => {
  requestWhatsAppPairingCode(event).catch((error) => setNotice(error.message || String(error), "error"));
});

el("whatsappLogoutButton").addEventListener("click", () => {
  clearWhatsAppSession().catch((error) => setNotice(error.message || String(error), "error"));
});

loadOverview().catch((error) => {
  setNotice(error.message || String(error), "error");
});

refreshTelegramAuth().catch(() => {});
refreshWhatsAppAuth().catch(() => {});
setInterval(() => {
  refreshWhatsAppAuth().catch(() => {});
}, 5000);
