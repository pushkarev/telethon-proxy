let selectedPeerId = null;
let activeSection = "configuration";

function el(id) { return document.getElementById(id); }
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
function setActiveSection(section) {
  activeSection = section;
  document.querySelectorAll(".folder-button").forEach((node) => {
    node.classList.toggle("active", node.dataset.section === section);
  });
  document.querySelectorAll(".section-panel").forEach((node) => {
    node.classList.toggle("active", node.dataset.sectionPanel === section);
  });
}

async function loadOverview() {
  const response = await fetch("/api/overview");
  const data = await response.json();
  renderOverview(data);
  if (!selectedPeerId && data.chats.length) {
    selectedPeerId = data.chats[0].peer_id;
  }
  renderChats(data.chats);
  renderApis(data.apis);
  if (selectedPeerId) {
    await loadChat(selectedPeerId);
  }
}

function renderOverview(data) {
  el("clientCount").textContent = data.clients.length;
  el("chatCount").textContent = data.chats.length;
  el("dashboardAddress").textContent = `${data.config.dashboard_host}:${data.config.dashboard_port}`;
  el("folderBadge").textContent = data.config.cloud_folder_name;
  el("configGrid").innerHTML = [
    ["Cloud folder", data.config.cloud_folder_name],
    ["MTProto endpoint", `${data.config.downstream_host}:${data.config.mtproto_port}`],
    ["Dashboard endpoint", `${data.config.dashboard_host}:${data.config.dashboard_port}`],
    ["Issued sessions", data.config.issued_client_count],
    ["Reconnect backoff", `${data.config.upstream_reconnect_min_delay}s → ${data.config.upstream_reconnect_max_delay}s`],
    ["Allow member listing", data.config.allow_member_listing ? "yes" : "no"],
    ["Proxy session label", data.config.downstream_session_label],
  ].map(([k, v]) => `<div>${esc(k)}</div><div>${esc(v)}</div>`).join("");
  el("upstreamGrid").innerHTML = [
    ["Name", data.upstream.name || "unknown"],
    ["Phone", data.upstream.phone || "unknown"],
    ["Username", data.upstream.username ? "@" + data.upstream.username : "none"],
  ].map(([k, v]) => `<div>${esc(k)}</div><div>${esc(v)}</div>`).join("");
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
  el("mcpGrid").innerHTML = [
    ["Endpoint", `http://${data.mcp.host}:${data.mcp.port}${data.mcp.path}`],
    ["Transport", data.mcp.transport],
    ["Auth", data.mcp.auth],
    ["Allowed origin", data.mcp.allowed_origin],
  ].map(([k, v]) => `<div>${esc(k)}</div><div>${esc(v)}</div>`).join("");
  el("mcpCard").innerHTML = `
    <div class="row">
      <div class="title">Bearer token</div>
      <span class="pill">local MCP</span>
    </div>
    <textarea class="credential-session" readonly>${esc(data.mcp.token)}</textarea>
    <div class="meta">
      Example endpoint: <span class="mono">${esc(`http://${data.mcp.host}:${data.mcp.port}${data.mcp.path}`)}</span><br />
      Suggested tools: <span class="mono">telegram.list_chats</span>, <span class="mono">telegram.get_messages</span>, <span class="mono">telegram.search_messages</span>, <span class="mono">telegram.send_message</span>, <span class="mono">telegram.delete_messages</span>, <span class="mono">telegram.mark_read</span>, <span class="mono">telegram.list_members</span><br />
      Subscriptions: open SSE with <span class="mono">Mcp-Session-Id</span> and subscribe to <span class="mono">telegram://updates</span> or <span class="mono">telegram://chat/&lt;peer_id&gt;</span>
    </div>
  `;

  if (data.error) {
    el("notice").style.display = "block";
    el("notice").textContent = data.error;
  } else {
    el("notice").style.display = "none";
  }

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
}

function renderChats(chats) {
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

  document.querySelectorAll(".chat").forEach((node) => {
    node.addEventListener("click", async () => {
      selectedPeerId = Number(node.dataset.peer);
      renderChats(chats);
      await loadChat(selectedPeerId);
    });
  });
}

async function loadChat(peerId) {
  const response = await fetch(`/api/chat?peer_id=${encodeURIComponent(peerId)}`);
  const data = await response.json();
  el("chatBadge").textContent = data.chat ? data.chat.title : "Recent history";
  el("messageHeading").textContent = data.chat ? data.chat.title : "Messages";
  el("chatKindPill").textContent = data.chat ? data.chat.kind : "chat";
  el("chatScreenMeta").innerHTML = data.chat
    ? `${data.chat.username ? "@" + esc(data.chat.username) + " · " : ""}peer ${esc(data.chat.peer_id)}`
    : "Pick a Cloud chat to inspect recent history.";

  if (!data.messages.length) {
    el("messageList").innerHTML = '<div class="empty">No recent messages were returned for this chat.</div>';
    return;
  }

  let lastDay = null;
  const html = [];
  for (const message of data.messages.slice().reverse()) {
    const day = fmtDay(message.date);
    if (day && day !== lastDay) {
      html.push(`<div class="day-stamp">${esc(day)}</div>`);
      lastDay = day;
    }
    html.push(`
      <div class="message-row ${message.out ? "outgoing" : "incoming"}">
        <div class="message-bubble">
          <div class="message-text">${esc(message.text || "[non-text message]")}</div>
          <div class="message-meta">
            ${message.media ? `<span class="message-chip">${esc(message.media)}</span>` : ""}
            <span>${esc(fmtTime(message.date))}</span>
          </div>
        </div>
      </div>
    `);
  }
  el("messageList").innerHTML = html.join("");
  el("messageList").scrollTop = el("messageList").scrollHeight;
}

function renderApis(apis) {
  el("forwardedApis").innerHTML = apis.forwarded.map((name) => `<div class="api"><code>${esc(name)}</code></div>`).join("");
  el("localApis").innerHTML = apis.proxy_local.map((name) => `<div class="api"><code>${esc(name)}</code></div>`).join("");
}

document.querySelectorAll(".folder-button").forEach((node) => {
  node.addEventListener("click", () => setActiveSection(node.dataset.section));
});

loadOverview().catch((error) => {
  el("notice").style.display = "block";
  el("notice").textContent = error.message || String(error);
});
