const state = {
  selectedPeerId: null,
  activeSection: "configuration",
  chatFilter: "",
  overview: null,
};

function el(id) {
  return document.getElementById(id);
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function fmtDateTime(value) {
  if (!value) return "unknown";
  return new Date(value).toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function fmtTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function fmtDay(value) {
  if (!value) return "";
  return new Date(value).toLocaleDateString([], {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function fmtRelative(value) {
  if (!value) return "Unknown";
  const when = new Date(value);
  const diffSeconds = Math.round((when.getTime() - Date.now()) / 1000);
  const ranges = [
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [unit, size] of ranges) {
    if (Math.abs(diffSeconds) >= size || unit === "minute") {
      const amount = Math.round(diffSeconds / size);
      return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(amount, unit);
    }
  }
  return "just now";
}

function matchText(value, query) {
  return String(value ?? "").toLowerCase().includes(query);
}

function renderKvList(rows) {
  return rows
    .map(
      ([key, value]) => `
        <div class="kv-item">
          <div class="kv-key">${esc(key)}</div>
          <div class="kv-value">${esc(value)}</div>
        </div>
      `,
    )
    .join("");
}

function setNotice(message) {
  const notice = el("notice");
  if (!message) {
    notice.hidden = true;
    notice.textContent = "";
    return;
  }
  notice.hidden = false;
  notice.textContent = message;
}

function setActiveSection(section) {
  state.activeSection = section;
  document.querySelectorAll(".folder-button").forEach((node) => {
    node.classList.toggle("active", node.dataset.section === section);
  });
  document.querySelectorAll(".section-panel").forEach((node) => {
    node.classList.toggle("active", node.dataset.sectionPanel === section);
  });
}

async function loadOverview() {
  const response = await fetch("/api/overview");
  if (!response.ok) {
    throw new Error(`Dashboard request failed with status ${response.status}`);
  }

  const data = await response.json();
  state.overview = data;
  if (!state.selectedPeerId && data.chats.length) {
    state.selectedPeerId = data.chats[0].peer_id;
  }
  renderOverview(data);
  renderChats(data.chats);
  renderApis(data.apis);

  if (state.selectedPeerId) {
    await loadChat(state.selectedPeerId);
    return;
  }

  el("messageList").innerHTML = '<div class="empty">No Cloud chats are currently available to inspect.</div>';
}

function renderOverview(data) {
  const forwardedCount = data.apis.forwarded.length;
  const localCount = data.apis.proxy_local.length;
  const serviceStatus = el("serviceStatus");

  if (data.error) {
    serviceStatus.textContent = "Attention needed";
    serviceStatus.className = "status-pill danger";
  } else if (data.clients.length) {
    serviceStatus.textContent = "Healthy";
    serviceStatus.className = "status-pill";
  } else {
    serviceStatus.textContent = "Monitoring";
    serviceStatus.className = "status-pill warning";
  }

  el("generatedAtLabel").textContent = `Updated ${fmtRelative(data.generated_at)}`;
  el("clientCount").textContent = String(data.clients.length);
  el("clientCountNote").textContent = data.clients.length
    ? `${data.clients.filter((client) => client.authorized).length} authorized session${data.clients.filter((client) => client.authorized).length === 1 ? "" : "s"}`
    : "No active sessions";
  el("chatCount").textContent = String(data.chats.length);
  el("chatCountNote").textContent = `${data.config.cloud_folder_name} folder scope`;
  el("apiSurfaceCount").textContent = String(forwardedCount + localCount);
  el("apiSurfaceNote").textContent = `${forwardedCount} forwarded, ${localCount} local`;
  el("dashboardAddress").textContent = `${data.config.dashboard_host}:${data.config.dashboard_port}`;
  el("folderBadge").textContent = data.config.cloud_folder_name;

  el("overviewCards").innerHTML = [
    {
      label: "MTProto endpoint",
      value: `${data.config.downstream_host}:${data.config.mtproto_port}`,
      note: "Downstream clients connect here.",
    },
    {
      label: "MCP endpoint",
      value: `http://${data.mcp.host}:${data.mcp.port}${data.mcp.path}`,
      note: `${data.mcp.transport} with bearer auth.`,
    },
    {
      label: "Upstream account",
      value: data.upstream.username ? `@${data.upstream.username}` : data.upstream.name || "Unavailable",
      note: data.upstream.phone || "Phone unavailable",
    },
    {
      label: "Issued sessions",
      value: String(data.config.issued_client_count),
      note: `Reconnect backoff ${data.config.upstream_reconnect_min_delay}s to ${data.config.upstream_reconnect_max_delay}s`,
    },
  ]
    .map(
      (card) => `
        <div class="hero-detail">
          <div class="hero-detail-label">${esc(card.label)}</div>
          <div class="hero-detail-value">${esc(card.value)}</div>
          <div class="hero-detail-note">${esc(card.note)}</div>
        </div>
      `,
    )
    .join("");

  el("configGrid").innerHTML = renderKvList([
    ["Cloud folder", data.config.cloud_folder_name],
    ["MTProto endpoint", `${data.config.downstream_host}:${data.config.mtproto_port}`],
    ["Dashboard endpoint", `${data.config.dashboard_host}:${data.config.dashboard_port}`],
    ["Proxy session label", data.config.downstream_session_label],
    ["Issued sessions", data.config.issued_client_count],
    ["Allow member listing", data.config.allow_member_listing ? "Yes" : "No"],
    ["Reconnect backoff", `${data.config.upstream_reconnect_min_delay}s to ${data.config.upstream_reconnect_max_delay}s`],
  ]);

  el("upstreamGrid").innerHTML = renderKvList([
    ["Name", data.upstream.name || "Unknown"],
    ["Phone", data.upstream.phone || "Unknown"],
    ["Username", data.upstream.username ? `@${data.upstream.username}` : "None"],
  ]);

  el("credentialList").innerHTML = data.downstream_credentials.length
    ? data.downstream_credentials
        .map(
          (cred) => `
            <div class="credential-card">
              <div class="row">
                <div class="title">${esc(cred.label)}</div>
                <span class="pill">${cred.phone ? "authorized" : "issued"}</span>
              </div>
              <div class="kv">
                ${renderKvList([
                  ["Host", cred.host || data.config.downstream_host],
                  ["Port", cred.port || data.config.mtproto_port],
                  ["API ID", data.config.downstream_api_id],
                  ["API hash", data.config.downstream_api_hash],
                  ["Proxy phone", data.config.downstream_login_phone],
                  ["Proxy code", data.config.downstream_login_code],
                ])}
              </div>
              <div class="meta">
                Created ${esc(fmtDateTime(cred.created_at))}${cred.phone ? `<br />Bound phone ${esc(cred.phone)}` : ""}
              </div>
              ${
                cred.session_string
                  ? `<textarea class="credential-session" readonly>${esc(cred.session_string)}</textarea>`
                  : '<div class="empty">Session string was not retained for this issued client.</div>'
              }
            </div>
          `,
        )
        .join("")
    : '<div class="empty">No downstream client credentials have been issued yet.</div>';

  el("clientList").innerHTML = data.clients.length
    ? data.clients
        .map(
          (client) => `
            <div class="client">
              <div class="row">
                <div class="title">${esc(client.label)}</div>
                <span class="pill">${client.authorized ? "authorized" : "pending"}</span>
              </div>
              <div class="meta">
                ${esc(client.remote_addr)}<br />
                Connected ${esc(fmtDateTime(client.connected_at))}<br />
                ${client.phone ? `Phone ${esc(client.phone)}<br />` : ""}
                Key <span class="mono">${esc(client.key_id)}</span>
              </div>
            </div>
          `,
        )
        .join("")
    : '<div class="empty">No clients are connected right now.</div>';

  el("mcpGrid").innerHTML = renderKvList([
    ["Endpoint", `http://${data.mcp.host}:${data.mcp.port}${data.mcp.path}`],
    ["Transport", data.mcp.transport],
    ["Authorization", data.mcp.auth],
    ["Allowed origin", data.mcp.allowed_origin],
  ]);

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

  setNotice(data.error);
}

function renderChats(chats) {
  const query = state.chatFilter.trim().toLowerCase();
  const filtered = chats.filter((chat) =>
    !query ||
    matchText(chat.title, query) ||
    matchText(chat.username, query) ||
    matchText(chat.peer_id, query) ||
    matchText(chat.kind, query),
  );

  el("chatListMeta").textContent = query
    ? `${filtered.length} matching chat${filtered.length === 1 ? "" : "s"} of ${chats.length}`
    : `${chats.length} chat${chats.length === 1 ? "" : "s"} in ${el("folderBadge").textContent}`;

  el("chatList").innerHTML = filtered.length
    ? filtered
        .map(
          (chat) => `
            <div class="chat ${state.selectedPeerId === chat.peer_id ? "active" : ""}" data-peer="${chat.peer_id}">
              <div class="row">
                <div class="title">${esc(chat.title)}</div>
                <span class="pill">${esc(chat.kind)}</span>
              </div>
              <div class="meta">
                ${chat.username ? `@${esc(chat.username)}<br />` : ""}
                Peer ${esc(chat.peer_id)}
              </div>
            </div>
          `,
        )
        .join("")
    : `<div class="empty">${query ? "No chats match this filter." : "No Cloud chats are currently visible."}</div>`;

  document.querySelectorAll(".chat").forEach((node) => {
    node.addEventListener("click", async () => {
      state.selectedPeerId = Number(node.dataset.peer);
      renderChats(chats);
      await loadChat(state.selectedPeerId);
    });
  });
}

async function loadChat(peerId) {
  const response = await fetch(`/api/chat?peer_id=${encodeURIComponent(peerId)}`);
  if (!response.ok) {
    throw new Error(`Chat request failed with status ${response.status}`);
  }

  const data = await response.json();
  el("chatBadge").textContent = data.chat ? data.chat.title : "Recent history";
  el("messageHeading").textContent = data.chat ? data.chat.title : "Messages";
  el("chatKindPill").textContent = data.chat ? data.chat.kind : "chat";
  el("chatScreenMeta").innerHTML = data.chat
    ? `${data.chat.username ? `@${esc(data.chat.username)} · ` : ""}Peer ${esc(data.chat.peer_id)}`
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
  el("forwardedApis").innerHTML = apis.forwarded.length
    ? apis.forwarded.map((name) => `<div class="api"><code>${esc(name)}</code></div>`).join("")
    : '<div class="empty">No forwarded upstream methods are configured.</div>';

  el("localApis").innerHTML = apis.proxy_local.length
    ? apis.proxy_local.map((name) => `<div class="api"><code>${esc(name)}</code></div>`).join("")
    : '<div class="empty">No proxy-local methods are configured.</div>';
}

document.querySelectorAll(".folder-button").forEach((node) => {
  node.addEventListener("click", () => setActiveSection(node.dataset.section));
});

el("chatFilter").addEventListener("input", () => {
  state.chatFilter = el("chatFilter").value;
  if (state.overview) {
    renderChats(state.overview.chats);
  }
});

loadOverview().catch((error) => {
  setNotice(error.message || String(error));
});
