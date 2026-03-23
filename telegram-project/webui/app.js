let selectedPeerId = null;
let selectedWhatsAppJid = null;
let selectedIMessageChatId = null;
let activeSection = "telegram";
let activeTelegramPane = "chats";
let activeWhatsAppPane = "chats";
let activeIMessagePane = "all-chats";
let telegramAuthState = null;
let whatsappAuthState = null;
let imessageAuthState = null;
let desktopRuntimeState = {
  backgroundOwner: "external",
  platform: "unknown",
};

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

const DEFAULT_IMESSAGE_AUTH = {
  enabled: false,
  available: true,
  connected: false,
  has_session: false,
  messages_app_accessible: false,
  database_accessible: false,
  messages_app_error: null,
  database_error: null,
  automation_hint: "",
  db_path: "",
  accounts: [],
  all_chats: [],
  visible_chats: [],
  visible_chat_ids: [],
  chats: [],
  last_error: null,
};

function el(id) { return document.getElementById(id); }

function setText(id, value) {
  const node = el(id);
  if (node) {
    node.textContent = value;
  }
}

function setHtml(id, value) {
  const node = el(id);
  if (node) {
    node.innerHTML = value;
  }
}

function formatApiError(url, status, payload) {
  if (status === 404 && String(url).startsWith("/api/telegram/auth")) {
    return "Telegram Settings needs the newer background service. Restart the app or local proxy service and try again.";
  }
  if (status === 404 && String(url).startsWith("/api/whatsapp/auth")) {
    return "WhatsApp support needs the newer background service. Restart the app or local proxy service and try again.";
  }
  if (status === 404 && String(url).startsWith("/api/imessage/auth")) {
    return "iMessage support needs the newer background service. Restart the app or local proxy service and try again.";
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

function fmtListDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) {
    return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  if (date.getFullYear() === now.getFullYear()) {
    return date.toLocaleDateString([], { month: "short", day: "numeric" });
  }
  return date.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
}

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function compactLastSeen(value) {
  return value ? `last ${fmtDate(value)}` : "no recent messages";
}

function firstNonEmpty(...values) {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) return text;
  }
  return "";
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

function setActiveWhatsAppPane(pane) {
  activeWhatsAppPane = pane;
  document.querySelectorAll(".whatsapp-pane-button").forEach((node) => {
    node.classList.toggle("active", node.dataset.whatsappPane === pane);
  });
  document.querySelectorAll(".whatsapp-pane").forEach((node) => {
    node.classList.toggle("active", node.dataset.whatsappPanePanel === pane);
  });
}

function setActiveIMessagePane(pane) {
  activeIMessagePane = pane;
  document.querySelectorAll(".imessage-pane-button").forEach((node) => {
    node.classList.toggle("active", node.dataset.imessagePane === pane);
  });
  document.querySelectorAll(".imessage-pane").forEach((node) => {
    node.classList.toggle("active", node.dataset.imessagePanePanel === pane);
  });
  const enabledShell = el("imessageEnabledShell");
  if (enabledShell) {
    enabledShell.hidden = !imessageAuthState?.enabled;
  }
  const shell = el("imessageChatShell");
  if (shell) {
    shell.classList.toggle("hidden", !imessageAuthState?.enabled || pane === "settings");
  }
}

function getIMessageAllChats(state = imessageAuthState) {
  if (Array.isArray(state?.all_chats)) {
    return state.all_chats;
  }
  if (Array.isArray(state?.chats)) {
    return state.chats;
  }
  return [];
}

function getIMessageVisibleChats(state = imessageAuthState) {
  if (Array.isArray(state?.visible_chats)) {
    return state.visible_chats;
  }
  if (Array.isArray(state?.chats)) {
    return state.chats;
  }
  return [];
}

function getIMessageVisibleChatIds(state = imessageAuthState) {
  if (Array.isArray(state?.visible_chat_ids)) {
    return state.visible_chat_ids;
  }
  return getIMessageVisibleChats(state).map((chat) => chat.chat_id);
}

function bindIMessageEnabledToggle(id, enabled) {
  const toggle = el(id);
  if (!toggle) return;
  toggle.checked = Boolean(enabled);
  toggle.onchange = async () => {
    toggle.disabled = true;
    try {
      await setIMessageEnabled(toggle.checked);
    } catch (error) {
      toggle.checked = Boolean(imessageAuthState?.enabled);
      setNotice(error.message || String(error), "error");
    } finally {
      toggle.disabled = false;
    }
  };
}

function setConnectionCheck(id, connected, label) {
  const node = el(id);
  if (!node) {
    return;
  }
  node.hidden = !connected;
  if (connected) {
    const statusText = `${label} connected`;
    node.setAttribute("title", statusText);
    node.setAttribute("aria-label", statusText);
  } else {
    node.removeAttribute("title");
    node.removeAttribute("aria-label");
  }
}

function syncConnectionChecks({
  telegramAuth = telegramAuthState,
  whatsappAuth = whatsappAuthState,
  imessageAuth = imessageAuthState,
} = {}) {
  setConnectionCheck(
    "telegramConnectionCheck",
    Boolean(telegramAuth?.has_session && telegramAuth?.next_step === "ready"),
    "Telegram",
  );
  setConnectionCheck(
    "whatsappConnectionCheck",
    Boolean(whatsappAuth?.connected),
    "WhatsApp",
  );
  setConnectionCheck(
    "imessageConnectionCheck",
    Boolean(imessageAuth?.connected),
    "iMessage",
  );
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

async function loadDesktopRuntime() {
  if (!bridge?.runtimeInfo) {
    return desktopRuntimeState;
  }
  try {
    desktopRuntimeState = {
      ...desktopRuntimeState,
      ...(await bridge.runtimeInfo()),
    };
  } catch {
    // Leave the default runtime info in place when running outside Electron.
  }
  return desktopRuntimeState;
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
    imessage: {
      ...DEFAULT_IMESSAGE_AUTH,
      ...(raw.imessage || {}),
    },
  };

  renderOverview(data);
  renderChats(data.chats);
  renderTelegramAuth(data.telegram_auth);
  renderWhatsAppAuth(data.whatsapp);
  renderIMessageAuth(data.imessage);

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

  if (!data.imessage.enabled) {
    selectedIMessageChatId = null;
  } else {
    const imessageAllChats = getIMessageAllChats(data.imessage);
    const imessageVisibleChats = getIMessageVisibleChats(data.imessage);
    const preferredIMessageChats = activeIMessagePane === "visible-chats" ? imessageVisibleChats : imessageAllChats;
    const imessageAllChatIds = new Set(imessageAllChats.map((chat) => chat.chat_id));
    if (selectedIMessageChatId && !imessageAllChatIds.has(selectedIMessageChatId)) {
      selectedIMessageChatId = null;
    }
    if (activeIMessagePane === "visible-chats") {
      const visibleChatIds = new Set(imessageVisibleChats.map((chat) => chat.chat_id));
      if (selectedIMessageChatId && !visibleChatIds.has(selectedIMessageChatId)) {
        selectedIMessageChatId = null;
      }
    }
    if (!selectedIMessageChatId && preferredIMessageChats.length) {
      selectedIMessageChatId = preferredIMessageChats[0].chat_id;
      await loadIMessageChat(selectedIMessageChatId);
    } else if (selectedIMessageChatId) {
      await loadIMessageChat(selectedIMessageChatId);
    }
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
  const imessageAuth = {
    ...DEFAULT_IMESSAGE_AUTH,
    ...(data.imessage || {}),
  };
  const telegramChatCount = Array.isArray(data.chats) ? data.chats.length : 0;
  const whatsappChatCount = Array.isArray(whatsappAuth.chats) ? whatsappAuth.chats.length : 0;
  const imessageChatCount = getIMessageVisibleChats(imessageAuth).length;

  setText("chatCount", telegramChatCount + whatsappChatCount + imessageChatCount);
  setText("telegramChatsTab", `Chats (${telegramChatCount})`);
  setText("dashboardAddress", `${data.config.dashboard_host}:${data.config.dashboard_port}`);
  setText("folderBadge", data.config.cloud_folder_name);
  setText("heroScopePill", `${data.config.cloud_folder_name} scope`);
  setText("telegramFolderName", `Telegram (${telegramChatCount})`);
  setText("whatsappFolderName", `WhatsApp (${whatsappChatCount})`);
  setText("imessageFolderName", `Messages (${imessageChatCount})`);
  setText("heroTelegramPill", telegramAuth.has_session ? "Telegram ready" : "Telegram login needed");
  setText(
    "heroWhatsAppPill",
    whatsappAuth.connected ? "WhatsApp connected" : (whatsappAuth.has_session ? "WhatsApp reconnect needed" : "WhatsApp QR needed"),
  );
  setText(
    "heroIMessagePill",
    !imessageAuth.enabled
      ? "Messages off"
      : (imessageAuth.connected ? "Messages connected" : (imessageAuth.messages_app_accessible ? "Messages local access" : "Messages permission needed")),
  );
  setText("telegramBadge", telegramAuth.has_session ? "Authorized" : "Needs login");
  setText("whatsappBadge", whatsappAuth.connected ? "Connected" : (whatsappAuth.has_session ? "Reconnect needed" : "Needs QR"));
  setText(
    "imessageBadge",
    !imessageAuth.enabled ? "Off" : (imessageAuth.connected ? "Connected" : (imessageAuth.messages_app_accessible ? "Local access" : "Permission needed")),
  );
  syncConnectionChecks({ telegramAuth, whatsappAuth, imessageAuth });

  const mcpScheme = String(data.mcp.scheme || "http").toLowerCase() === "https" ? "https" : "http";
  const mcpEndpoint = data.mcp.endpoint || `${mcpScheme}://${data.mcp.host}:${data.mcp.port}${data.mcp.path}`;
  el("mcpGrid").innerHTML = [
    ["Endpoint", mcpEndpoint],
    ["Listener", data.mcp.listening ? "listening" : "offline"],
    ["Transport", data.mcp.transport],
    ["Auth", data.mcp.auth],
    ["Allowed origin", data.mcp.allowed_origin],
  ].map(([key, value]) => `<div>${esc(key)}</div><div>${esc(value)}</div>`).join("");

  const bindOptions = Array.isArray(data.mcp.bind_options) ? data.mcp.bind_options : [];
  el("mcpListenerCard").innerHTML = `
    <div class="info-card-head">
      <div class="section-kicker">Listener</div>
      <h3>Bind interface and port</h3>
    </div>
    <form id="mcpListenerForm" class="mcp-settings-form">
      <label class="field">
        <span>Protocol</span>
        <select id="mcpSchemeSelect" name="scheme">
          <option value="http"${mcpScheme === "http" ? " selected" : ""}>HTTP</option>
          <option value="https"${mcpScheme === "https" ? " selected" : ""}>HTTPS</option>
        </select>
      </label>
      <label class="field">
        <span>Interface</span>
        <select id="mcpHostSelect" name="host">
          ${bindOptions.map((option) => `
            <option value="${esc(option.host)}"${option.host === data.mcp.host ? " selected" : ""}>
              ${esc(option.label || option.host)}
            </option>
          `).join("")}
        </select>
      </label>
      <label class="field">
        <span>Port</span>
        <input id="mcpPortInput" name="port" type="text" inputmode="numeric" value="${esc(data.mcp.port)}" autocomplete="off" />
      </label>
      <div class="meta">
        Pick the protocol, interface, and port you want MCP to bind to. Localhost keeps it private to this Mac; the Tailscale or network interfaces make it reachable from other devices on that network. HTTPS requires the backend to have TLS files configured through <span class="mono">TP_MCP_TLS_CERT</span> and <span class="mono">TP_MCP_TLS_KEY</span>.
      </div>
      <div class="auth-actions">
        <button type="submit" class="primary-button" id="applyMcpConfigButton">Apply and restart MCP</button>
        <span class="pill">${data.mcp.listening ? "Listening" : "Offline"}</span>
      </div>
    </form>
  `;

  el("mcpCard").innerHTML = `
    <div class="row">
      <div class="title">Bearer token</div>
      <span class="pill">local MCP</span>
    </div>
    <div class="meta">
      The bearer token stays hidden in the app. Use copy when you need it, or revoke it to invalidate the current token and copy a fresh one.<br />
      Example endpoint: <span class="mono">${esc(mcpEndpoint)}</span><br />
      Suggested tools: <span class="mono">telegram.list_chats</span>, <span class="mono">whatsapp.list_chats</span>, <span class="mono">imessage.list_chats</span>, <span class="mono">imessage.get_messages</span>, <span class="mono">imessage.send_message</span><br />
      Resources: <span class="mono">telegram://config</span>, <span class="mono">whatsapp://config</span>, <span class="mono">imessage://config</span>, <span class="mono">imessage://chat/&lt;chat_id&gt;</span><br />
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

  const mcpListenerForm = el("mcpListenerForm");
  if (mcpListenerForm) {
    mcpListenerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = el("applyMcpConfigButton");
      button.disabled = true;
      try {
        await setMcpConfig(
          el("mcpHostSelect").value.trim(),
          el("mcpPortInput").value.trim(),
          el("mcpSchemeSelect").value.trim(),
        );
      } catch (error) {
        setNotice(error.message || "Could not update MCP listener settings.", "error");
      } finally {
        button.disabled = false;
      }
    });
  }

  if (data.error) {
    setNotice(data.error, "error");
  } else if (!telegramAuth.last_error && !whatsappAuth.last_error && !imessageAuth.last_error) {
    setNotice("");
  }
}

function renderChats(chats) {
  if (!chats.length) {
    selectedPeerId = null;
  }

  el("chatList").innerHTML = chats.length
    ? chats.map((chat) => `
        <div class="chat chat-compact ${selectedPeerId === chat.peer_id ? "active" : ""}" data-peer="${chat.peer_id}">
          <div class="row">
            <div class="title">${esc(chat.title)}</div>
            <div class="row chat-title-actions">
              <span class="meta chat-inline-date">${esc(fmtListDate(chat.last_message_at) || "no recent")}</span>
              <span class="pill">${esc(chat.kind)}</span>
            </div>
          </div>
          <div class="meta chat-compact-meta">
            ${esc(firstNonEmpty(chat.username ? `@${chat.username}` : "", `peer ${chat.peer_id}`))}<br />
            ${esc(compactLastSeen(chat.last_message_at))}
          </div>
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
        <div class="chat chat-compact ${selectedWhatsAppJid === chat.jid ? "active" : ""}" data-jid="${esc(chat.jid)}">
          <div class="row">
            <div class="title">${esc(chat.title)}</div>
            <div class="row chat-title-actions">
              <span class="meta chat-inline-date">${esc(fmtListDate(chat.last_message_at) || "no recent")}</span>
              <span class="pill">${esc(chat.kind)}</span>
            </div>
          </div>
          <div class="meta chat-compact-meta">
            ${esc(chat.jid)}<br />
            ${esc(compactLastSeen(chat.last_message_at))}
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

function renderIMessageChats(listId, chats, {
  emptyText,
  showVisibilityToggle = false,
  visibleChatIds = [],
  compact = false,
} = {}) {
  const container = el(listId);
  const visibleSet = new Set(visibleChatIds);
  container.innerHTML = chats.length
    ? chats.map((chat) => `
        <div class="chat ${compact ? "chat-compact" : ""} ${selectedIMessageChatId === chat.chat_id ? "active" : ""}" data-chat-id="${esc(chat.chat_id)}">
          <div class="row">
            <div class="title">${esc(chat.title)}</div>
            <div class="row chat-title-actions">
              <span class="meta chat-inline-date">${esc(fmtListDate(chat.last_message_at) || "no recent")}</span>
              ${showVisibilityToggle
                ? `<label class="chat-visibility-toggle${compact ? " compact-toggle" : ""}">
                    <input
                      type="checkbox"
                      data-chat-visibility="${esc(chat.chat_id)}"
                      aria-label="Visible through MCP"
                      title="Visible through MCP"
                      ${visibleSet.has(chat.chat_id) ? " checked" : ""}
                    />
                    ${compact ? "" : "<span>Visible via MCP</span>"}
                  </label>`
                : ""}
              ${compact ? "" : `<span class="pill">${esc(chat.kind)}</span>`}
            </div>
          </div>
          <div class="meta ${compact ? "chat-compact-meta" : ""}">
            ${esc(firstNonEmpty(chat.participants?.[0], chat.chat_id))}<br />
            ${esc(compactLastSeen(chat.last_message_at))}
          </div>
        </div>
      `).join("")
    : `<div class="empty">${esc(emptyText || "No local Messages chats are available.")}</div>`;

  container.querySelectorAll(".chat").forEach((node) => {
    node.addEventListener("click", async () => {
      selectedIMessageChatId = node.dataset.chatId;
      renderIMessageViews();
      await loadIMessageChat(selectedIMessageChatId);
    });
  });

  container.querySelectorAll("[data-chat-visibility]").forEach((input) => {
    input.addEventListener("click", (event) => {
      event.stopPropagation();
    });
    input.addEventListener("change", async (event) => {
      event.stopPropagation();
      const target = event.currentTarget;
      target.disabled = true;
      try {
        await setIMessageChatVisibility(target.dataset.chatVisibility, target.checked);
      } catch (error) {
        setNotice(error.message || "Could not update Messages MCP visibility.", "error");
      } finally {
        target.disabled = false;
      }
    });
  });
}

function renderIMessageViews() {
  const allChats = getIMessageAllChats();
  const visibleChats = getIMessageVisibleChats();
  const visibleChatIds = getIMessageVisibleChatIds();
  renderIMessageChats("imessageAllChatList", allChats, {
    emptyText: "No local Messages chats are available yet.",
    showVisibilityToggle: true,
    visibleChatIds,
    compact: true,
  });
  renderIMessageChats("imessageVisibleChatList", visibleChats, {
    emptyText: "No Messages chats are currently visible through MCP.",
    visibleChatIds,
    compact: true,
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
  const orderedMessages = messages
    .map((message, index) => ({ message, index }))
    .sort((left, right) => {
      const leftDate = left.message?.date ? new Date(left.message.date).getTime() : Number.NaN;
      const rightDate = right.message?.date ? new Date(right.message.date).getTime() : Number.NaN;
      const leftValid = Number.isFinite(leftDate);
      const rightValid = Number.isFinite(rightDate);
      if (leftValid && rightValid && leftDate !== rightDate) {
        return leftDate - rightDate;
      }
      if (leftValid !== rightValid) {
        return leftValid ? -1 : 1;
      }
      return left.index - right.index;
    })
    .map(({ message }) => message);
  for (const message of orderedMessages) {
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
      ? [
          data.chat.username ? `@${esc(data.chat.username)}` : "",
          `peer ${esc(data.chat.peer_id)}`,
          data.chat.last_message_at ? `last ${esc(fmtDate(data.chat.last_message_at))}` : "",
        ].filter(Boolean).join(" · ")
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
      ? [
          esc(data.chat.jid),
          data.chat.service_type ? `service ${esc(data.chat.service_type)}` : "",
          data.chat.labels?.length ? `labels ${esc(data.chat.labels.join(", "))}` : "",
          data.chat.last_message_at ? `last ${esc(fmtDate(data.chat.last_message_at))}` : "",
        ].filter(Boolean).join(" · ")
      : null,
  });
}

async function loadIMessageChat(chatId) {
  if (!chatId) {
    return;
  }
  const data = await getJson(`/api/imessage/chat?chat_id=${encodeURIComponent(chatId)}`);
  if (data.error) {
    renderMessageTimeline([], {
      headingId: "imessageMessageHeading",
      kindId: "imessageChatKindPill",
      metaId: "imessageChatScreenMeta",
      listId: "imessageMessageList",
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
    headingId: "imessageMessageHeading",
    kindId: "imessageChatKindPill",
    metaId: "imessageChatScreenMeta",
    listId: "imessageMessageList",
    fallbackHeading: "Messages",
    fallbackKind: "chat",
    fallbackMeta: "Pick a Messages chat to inspect recent local history.",
    title: data.chat ? data.chat.title : null,
    kind: data.chat ? data.chat.kind : null,
    meta: data.chat
      ? [
          data.chat.service_type ? `service ${esc(data.chat.service_type)}` : "",
          data.chat.participants?.length ? `participants ${esc(data.chat.participants.join(", "))}` : "",
          esc(data.chat.chat_id),
          data.chat.last_message_at ? `last ${esc(fmtDate(data.chat.last_message_at))}` : "",
        ].filter(Boolean).join(" · ")
      : null,
  });
}

async function setIMessageChatVisibility(chatId, visible) {
  await postJson("/api/imessage/visible-chats", {
    chat_id: chatId,
    visible,
  });
  await loadOverview();
  setNotice(
    visible
      ? "Messages chat is now visible through MCP."
      : "Messages chat was removed from MCP visibility.",
    "success",
  );
}

async function syncIMessageSelectionForActivePane() {
  const allChats = getIMessageAllChats();
  const visibleChats = getIMessageVisibleChats();
  const sourceChats = activeIMessagePane === "visible-chats" ? visibleChats : allChats;
  const sourceIds = new Set(sourceChats.map((chat) => chat.chat_id));
  if (selectedIMessageChatId && !sourceIds.has(selectedIMessageChatId)) {
    selectedIMessageChatId = null;
  }
  if (!selectedIMessageChatId && sourceChats.length) {
    selectedIMessageChatId = sourceChats[0].chat_id;
  }
  renderIMessageViews();
  if (selectedIMessageChatId && activeIMessagePane !== "settings") {
    await loadIMessageChat(selectedIMessageChatId);
    return;
  }
  if (!selectedIMessageChatId) {
    el("imessageMessageHeading").textContent = "Messages";
    el("imessageChatKindPill").textContent = "chat";
    el("imessageChatScreenMeta").textContent = activeIMessagePane === "visible-chats"
      ? "Choose a Messages chat that is visible through MCP to inspect recent local history."
      : "Pick a Messages chat to inspect recent local history.";
    el("imessageMessageList").innerHTML = activeIMessagePane === "visible-chats"
      ? '<div class="empty">No Messages chats are currently visible through MCP.</div>'
      : '<div class="empty">No Messages chat is selected yet.</div>';
  }
}

function renderTelegramAuth(state) {
  telegramAuthState = {
    ...DEFAULT_TELEGRAM_AUTH,
    ...(state || {}),
  };
  syncConnectionChecks({ telegramAuth: telegramAuthState });

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
  syncConnectionChecks({ whatsappAuth: whatsappAuthState });

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

function renderIMessageAuth(state) {
  imessageAuthState = {
    ...DEFAULT_IMESSAGE_AUTH,
    ...(state || {}),
  };
  syncConnectionChecks({ imessageAuth: imessageAuthState });

  const disabledCard = el("imessageDisabledCard");
  const enabledShell = el("imessageEnabledShell");
  const permissionShot = disabledCard?.querySelector(".permission-warning-shot");
  const allChats = getIMessageAllChats(imessageAuthState);
  const visibleChats = getIMessageVisibleChats(imessageAuthState);
  const accounts = Array.isArray(imessageAuthState.accounts) ? imessageAuthState.accounts : [];
  setText("imessageAllChatsTab", `All chats (${allChats.length})`);
  setText("imessageVisibleChatsTab", `Visible chats (${visibleChats.length})`);
  bindIMessageEnabledToggle("enableIMessageToggle", imessageAuthState.enabled);
  bindIMessageEnabledToggle("imessageEnabledSettingToggle", imessageAuthState.enabled);
  if (disabledCard) {
    disabledCard.hidden = Boolean(imessageAuthState.enabled);
  }
  if (permissionShot) {
    permissionShot.hidden = Boolean(imessageAuthState.messages_app_accessible);
  }
  if (enabledShell) {
    enabledShell.hidden = !imessageAuthState.enabled;
  }
  el("imessageBadge").textContent = !imessageAuthState.enabled
    ? "Off"
    : (imessageAuthState.connected ? "Connected" : (imessageAuthState.messages_app_accessible ? "Local access" : "Permission needed"));

  if (!imessageAuthState.enabled) {
    selectedIMessageChatId = null;
    el("imessageAuthStatus").innerHTML = `
      <div class="row">
        <div class="title">Messages access is off</div>
        <span class="pill">disabled</span>
      </div>
      <div class="meta">
        Enable Messages when you want Telethon Proxy to read local Messages chats or send through the Messages app. macOS will ask for Messages control access, and chat history may also require Full Disk Access.
      </div>
      ${imessageAuthState.messages_app_accessible ? '<div class="meta">Messages automation access is already granted on this Mac.</div>' : ""}
    `;
    el("imessageMessageHeading").textContent = "Messages";
    el("imessageChatKindPill").textContent = "off";
    el("imessageChatScreenMeta").textContent = "Enable Messages to inspect local chats and history.";
    el("imessageMessageList").innerHTML = '<div class="empty">Messages integration is currently turned off.</div>';
    return;
  }

  el("imessageAuthStatus").innerHTML = `
    <div class="row">
      <div class="title">Bridge status</div>
      <span class="pill">${imessageAuthState.connected ? "connected" : "local-only"}</span>
    </div>
    <div class="kv">
      <div>Messages automation</div><div>${imessageAuthState.messages_app_accessible ? "available" : "blocked"}</div>
      <div>History database</div><div>${imessageAuthState.database_accessible ? "available" : "blocked"}</div>
      <div>Accounts</div><div>${esc(accounts.length)}</div>
      <div>All chats</div><div>${esc(allChats.length)}</div>
      <div>Visible chats</div><div>${esc(visibleChats.length)}</div>
      <div>History path</div><div>${esc(imessageAuthState.db_path || "default")}</div>
    </div>
    ${accounts.length ? `<div class="meta">Messages accounts: ${esc(accounts.map((account) => `${account.description || account.id}${account.service_type ? ` (${account.service_type})` : ""}`).join(", "))}</div>` : ""}
    ${imessageAuthState.automation_hint ? `<div class="meta">${esc(imessageAuthState.automation_hint)}</div>` : ""}
    <div class="meta">Use <strong>All chats</strong> to choose which local threads should be visible through MCP. Only <strong>Visible chats</strong> are exposed there.</div>
    ${!imessageAuthState.database_accessible ? `
      <div class="inline-notice">
        Messages history is unavailable because Telethon Proxy cannot read <span class="mono">chat.db</span> yet.
        <a href="#" class="inline-link-action" id="openIMessageFilesAccessLink">Enable Full Disk Access</a>
      </div>
    ` : ""}
    ${!imessageAuthState.database_accessible ? `
      <div class="row">
        <button type="button" class="secondary-button compact-button" id="openIMessageAutomationButton">Open Automation</button>
        <button type="button" class="secondary-button compact-button" id="copyIMessageDbPathButton">Copy history path</button>
      </div>
    ` : ""}
    ${imessageAuthState.messages_app_error ? `<div class="inline-notice">${esc(imessageAuthState.messages_app_error)}</div>` : ""}
    ${!imessageAuthState.messages_app_error && imessageAuthState.database_error ? `<div class="inline-notice">${esc(imessageAuthState.database_error)}</div>` : ""}
    ${!imessageAuthState.database_accessible && desktopRuntimeState.backgroundOwner === "external"
      ? `<div class="inline-notice">This window is attached to an already-running background service. Grant the permission to that service context, or stop it and relaunch the desktop app so Telethon Proxy can own the local Messages reader.</div>`
      : ""}
  `;

  const openFilesAccessLink = document.getElementById("openIMessageFilesAccessLink");
  if (openFilesAccessLink) {
    openFilesAccessLink.addEventListener("click", async (event) => {
      event.preventDefault();
      try {
        if (!bridge?.openSystemSettings) {
          setNotice("Open the desktop app to jump directly to the macOS privacy settings.", "error");
          return;
        }
        await bridge.openSystemSettings("files");
      } catch (error) {
        setNotice(error.message || String(error), "error");
      }
    });
  }

  const openAutomationButton = document.getElementById("openIMessageAutomationButton");
  if (openAutomationButton) {
    openAutomationButton.addEventListener("click", async () => {
      try {
        if (!bridge?.openSystemSettings) {
          setNotice("Open the desktop app to jump directly to the macOS privacy settings.", "error");
          return;
        }
        await bridge.openSystemSettings("automation");
      } catch (error) {
        setNotice(error.message || String(error), "error");
      }
    });
  }

  const copyDbPathButton = document.getElementById("copyIMessageDbPathButton");
  if (copyDbPathButton) {
    copyDbPathButton.addEventListener("click", async () => {
      try {
        await copyText(imessageAuthState.db_path || "~/Library/Messages/chat.db");
        setNotice("Copied the Messages history path.", "success");
      } catch (error) {
        setNotice(error.message || String(error), "error");
      }
    });
  }

  renderIMessageViews();
  if (!selectedIMessageChatId) {
    el("imessageMessageHeading").textContent = "Messages";
    el("imessageChatKindPill").textContent = "chat";
    el("imessageChatScreenMeta").textContent = "Pick a Messages chat to inspect recent local history.";
    el("imessageMessageList").innerHTML = '<div class="empty">No Messages chat is selected yet.</div>';
  }
}

async function refreshTelegramAuth() {
  renderTelegramAuth(await getJson("/api/telegram/auth"));
}

async function refreshWhatsAppAuth() {
  renderWhatsAppAuth(await getJson("/api/whatsapp/auth"));
}

async function refreshIMessageAuth() {
  renderIMessageAuth(await getJson("/api/imessage/auth"));
  if (!imessageAuthState.enabled) {
    return;
  }
  await syncIMessageSelectionForActivePane();
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
  setActiveWhatsAppPane("settings");
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

async function setIMessageEnabled(enabled) {
  const result = await postJson("/api/imessage/enabled", { enabled });
  if (enabled) {
    setActiveSection("imessage");
    setActiveIMessagePane("settings");
  }
  setNotice(
    result.message || (enabled ? "Messages integration enabled." : "Messages integration disabled."),
    "success",
  );
  await loadOverview();
}

async function setMcpConfig(host, port, scheme) {
  const result = await postJson("/api/mcp/config", { host, port, scheme });
  setNotice(result.message || "MCP listener updated.", "success");
  await loadOverview();
}

document.querySelectorAll(".folder-button").forEach((node) => {
  node.addEventListener("click", () => setActiveSection(node.dataset.section));
});

document.querySelectorAll(".telegram-pane-button").forEach((node) => {
  node.addEventListener("click", () => setActiveTelegramPane(node.dataset.telegramPane));
});

document.querySelectorAll(".whatsapp-pane-button").forEach((node) => {
  node.addEventListener("click", () => setActiveWhatsAppPane(node.dataset.whatsappPane));
});

document.querySelectorAll(".imessage-pane-button").forEach((node) => {
  node.addEventListener("click", () => {
    setActiveIMessagePane(node.dataset.imessagePane);
    syncIMessageSelectionForActivePane().catch((error) => setNotice(error.message || String(error), "error"));
  });
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

loadDesktopRuntime()
  .then(() => loadOverview())
  .catch((error) => {
    setNotice(error.message || String(error), "error");
  });

refreshTelegramAuth().catch(() => {});
refreshWhatsAppAuth().catch(() => {});
refreshIMessageAuth().catch(() => {});
setInterval(() => {
  refreshWhatsAppAuth().catch(() => {});
  refreshIMessageAuth().catch(() => {});
}, 5000);
