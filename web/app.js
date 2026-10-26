// ShareLink DuoCast — Dashboard Client

// Which side of THIS screen you move the cursor off to reach a PC.
// Order is spatial so the row reads left, up, down, right.
const EDGE_BUTTONS = [
  { edge: "left",   glyph: "◀", title: "Reach this PC off my LEFT edge" },
  { edge: "top",    glyph: "▲", title: "Reach this PC off my TOP edge" },
  { edge: "bottom", glyph: "▼", title: "Reach this PC off my BOTTOM edge" },
  { edge: "right",  glyph: "▶", title: "Reach this PC off my RIGHT edge" },
];

class ShareLinkClient {
  constructor() {
    this.ws = null;
    this.reconnectTimer = null;
    this.state = null;

    this.initElements();
    this.bindEvents();
    this.connectWebSocket();
  }

  initElements() {
    // Header
    this.statusBadge = document.getElementById("connectionStatusBadge");
    this.statusText = document.getElementById("statusText");
    this.pingBadge = document.getElementById("pingBadge");
    this.btnEmergency = document.getElementById("btnEmergency");

    // Layout: one grid cell per screen edge. A peer is placed in the cell
    // matching the edge it was auto-assigned on connect.
    this.slots = {
      right: document.getElementById("slotRight"),
      left: document.getElementById("slotLeft"),
      top: document.getElementById("slotTop"),
      bottom: document.getElementById("slotBottom"),
    };
    this.peerCountBadge = document.getElementById("peerCountBadge");
    this.overflowPeers = document.getElementById("overflowPeers");

    this.localHostName = document.getElementById("localHostName");
    this.localIpBadge = document.getElementById("localIpBadge");
    this.localResBadge = document.getElementById("localResBadge");
    this.localFocusBadge = document.getElementById("localFocusBadge");

    // Discovery & Pairing
    this.discoveredCount = document.getElementById("discoveredCount");
    this.discoveryList = document.getElementById("discoveryList");
    this.emptyDiscovery = document.getElementById("emptyDiscovery");
    this.chkAutoAccept = document.getElementById("chkAutoAccept");
    this.pinCode = document.getElementById("pinCode");
    this.btnCopyPin = document.getElementById("btnCopyPin");

    // Manual connect
    this.inputTargetIp = document.getElementById("inputTargetIp");
    this.inputTargetPort = document.getElementById("inputTargetPort");
    this.inputTargetPin = document.getElementById("inputTargetPin");
    this.btnManualConnect = document.getElementById("btnManualConnect");

    // Stats & Tuning
    this.statLatency = document.getElementById("statLatency");
    this.statPackets = document.getElementById("statPackets");
    this.statClipboard = document.getElementById("statClipboard");
    this.statActiveMode = document.getElementById("statActiveMode");
    this.chkClipboardSync = document.getElementById("chkClipboardSync");

    this.rangeEdgeThreshold = document.getElementById("rangeEdgeThreshold");
    this.valEdgeThreshold = document.getElementById("valEdgeThreshold");
    this.rangeEdgeDelay = document.getElementById("rangeEdgeDelay");
    this.valEdgeDelay = document.getElementById("valEdgeDelay");
    this.btnTestClipboard = document.getElementById("btnTestClipboard");

    // Logs & Footer
    this.logStream = document.getElementById("logStream");
    this.btnClearLogs = document.getElementById("btnClearLogs");
    this.socketStatusText = document.getElementById("socketStatusText");

    // File Transfer
    this.btnPickSend = document.getElementById("btnPickSend");
    this.transferList = document.getElementById("transferList");
    this.noTransfers = document.getElementById("noTransfers");
    this.receiveDirLabel = document.getElementById("receiveDirLabel");
    this.dropOverlay = document.getElementById("dropOverlay");
    this.dropHint = document.getElementById("dropHint");
  }

  bindEvents() {
    // Delegated on document because renderPeers rebuilds the peer boxes on
    // every status tick - per-button listeners would be thrown away with them.
    document.addEventListener("click", (e) => {
      const edgeBtn = e.target.closest(".edge-btn");
      if (edgeBtn && edgeBtn.dataset.peerId) {
        this.sendAction("set_peer_edge", {
          peer_id: edgeBtn.dataset.peerId,
          edge: edgeBtn.dataset.edge,
        });
        return;
      }
      const focusBtn = e.target.closest("[data-focus-peer]");
      if (focusBtn) {
        this.sendAction("focus_peer", { peer_id: focusBtn.dataset.focusPeer });
      }
    });

    // Emergency Recenter
    this.btnEmergency.addEventListener("click", () => {
      this.sendAction("emergency");
    });

    // Minimize to tray (native window only - no-op in a browser tab)
    const btnTray = document.getElementById("btnMinimizeTray");
    btnTray.addEventListener("click", () => {
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.minimize_to_tray();
      } else {
        this.addLogEntry("Tray is only available in the native window.", "info");
      }
    });

    // Copy PIN
    this.btnCopyPin.addEventListener("click", () => {
      const pin = this.pinCode.innerText;
      if (pin && pin !== "------") {
        navigator.clipboard.writeText(pin);
        this.addLogEntry("PIN copied to clipboard", "info");
      }
    });

    // Manual Connect
    this.btnManualConnect.addEventListener("click", () => {
      const ip = this.inputTargetIp.value.trim();
      const port = parseInt(this.inputTargetPort.value.trim()) || 42898;
      const pin = this.inputTargetPin.value.trim();
      if (!ip) {
        alert("Please enter a valid IP address.");
        return;
      }
      this.sendAction("connect", { ip, port, pin });
    });

    // Auto-accept toggle
    this.chkAutoAccept.addEventListener("change", (e) => {
      this.sendAction("update_config", { auto_accept_lan: e.target.checked });
    });

    // Clipboard toggle
    this.chkClipboardSync.addEventListener("change", (e) => {
      this.sendAction("update_config", { clipboard_sync: e.target.checked });
    });

    // Sliders
    this.rangeEdgeThreshold.addEventListener("input", (e) => {
      this.valEdgeThreshold.innerText = `${e.target.value} px`;
    });
    this.rangeEdgeThreshold.addEventListener("change", (e) => {
      this.sendAction("update_config", { edge_threshold: parseInt(e.target.value) });
    });

    this.rangeEdgeDelay.addEventListener("input", (e) => {
      this.valEdgeDelay.innerText = `${e.target.value} ms`;
    });
    this.rangeEdgeDelay.addEventListener("change", (e) => {
      this.sendAction("update_config", { edge_delay_ms: parseInt(e.target.value) });
    });

    // Test clipboard
    this.btnTestClipboard.addEventListener("click", () => {
      const testMsg = `ShareLink Test: ${new Date().toLocaleTimeString()}`;
      navigator.clipboard.writeText(testMsg);
      this.addLogEntry(`Copied test string: "${testMsg}"`, "clipboard");
    });

    // Clear logs
    this.btnClearLogs.addEventListener("click", () => {
      this.logStream.innerHTML = "";
    });

    // File transfer: native picker when in the pywebview window, browser picker otherwise
    this.btnPickSend.addEventListener("click", async () => {
      if (window.pywebview) {
        const res = await fetch("/api/pick_and_send", { method: "POST" });
        const out = await res.json();
        if (out.status === "ERROR") this.addLogEntry(`Send failed: ${out.msg}`, "warning");
        return;
      }
      // Browser fallback
      const inp = document.createElement("input");
      inp.type = "file";
      inp.onchange = () => {
        if (inp.files.length) this.sendFileBytes(inp.files[0].name, inp.files[0]);
      };
      inp.click();
    });

    // Drag-and-drop send: drop files anywhere on the window. Targets the peer
    // currently holding the cursor, falling back to the first paired PC.
    let dragDepth = 0;
    window.addEventListener("dragenter", (e) => {
      e.preventDefault();
      dragDepth++;
      if (this.dropOverlay.classList.contains("hidden")) {
        const n = (this.state && this.state.peers || []).length;
        this.dropHint.innerText = n
          ? (this.state.active_peer_id ? "Will send to the PC holding the cursor" : "Will send to the paired PC")
          : "No PC paired yet";
        this.dropOverlay.classList.remove("hidden");
      }
    });
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("dragleave", (e) => {
      e.preventDefault();
      if (--dragDepth <= 0) { dragDepth = 0; this.dropOverlay.classList.add("hidden"); }
    });
    window.addEventListener("drop", (e) => {
      e.preventDefault();
      dragDepth = 0;
      this.dropOverlay.classList.add("hidden");
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      const s = this.state || {};
      const peerId = s.active_peer_id || ((s.peers || [])[0] || {}).device_id || "";
      if (!peerId) {
        this.addLogEntry("No PC paired - connect first, then drop files.", "warning");
        return;
      }
      for (const file of e.dataTransfer.files) this.sendFileBytes(file.name, file, peerId);
    });
  }

  async sendFileBytes(name, fileOrBytes, peerId = "") {
    // Upload to local engine, which forwards it to the peer. A File body streams
    // straight from disk; the legacy bytes path is kept for compatibility.
    const body = fileOrBytes instanceof Blob ? fileOrBytes : new Blob([fileOrBytes]);
    const headers = {
      "Content-Type": "application/octet-stream",
      "X-Filename": encodeURIComponent(name),
    };
    if (peerId) headers["X-Peer-Id"] = peerId;
    const res = await fetch("/api/upload_file", { method: "POST", headers, body });
    const out = await res.json();
    if (out.status !== "OK") this.addLogEntry(`Send failed: ${out.msg}`, "warning");
  }

  connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws`;

    this.socketStatusText.innerText = "Connecting to ShareLink Engine...";
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.socketStatusText.innerText = "Engine Connected • Ready";
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        this.handleMessage(msg);
      } catch (err) {
        console.error("WS Parse error:", err);
      }
    };

    this.ws.onclose = () => {
      this.socketStatusText.innerText = "Engine Disconnected • Reconnecting...";
      this.reconnectTimer = setTimeout(() => this.connectWebSocket(), 2000);
    };

    this.ws.onerror = () => {
      this.ws.close();
    };
  }

  sendAction(action, data = {}) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action, ...data }));
    }
  }

  handleMessage(msg) {
    if (msg.type === "status") {
      this.renderState(msg.data);
    } else if (msg.type === "log") {
      this.addLogEntry(msg.entry.msg, msg.entry.level, msg.entry.time);
    } else if (msg.type === "peer_discovered") {
      this.addLogEntry(`Nearby PC online: ${msg.peer.name}`, "info");
    }
  }

  renderState(state) {
    this.state = state;

    // Header Connection Pill
    const peers = state.peers || [];
    if (peers.length) {
      this.statusBadge.className = "status-pill status-connected";
      this.statusText.innerText = peers.length === 1
        ? `Paired with ${peers[0].name}`
        : `Paired with ${peers.length} PCs`;
      this.pingBadge.classList.remove("hidden");
      this.pingBadge.innerText = `${state.stats.latency_ms} ms`;
    } else {
      this.statusBadge.className = "status-pill status-disconnected";
      this.statusText.innerText = "Scanning Network for PCs...";
      this.pingBadge.classList.add("hidden");
    }

    // Local Monitor Info
    this.localHostName.innerText = state.device_name || "THIS PC";
    this.localIpBadge.innerText = `${state.local_ip}:${state.tcp_port}`;
    if (state.local_screen) {
      this.localResBadge.innerText = `${state.local_screen.width} × ${state.local_screen.height}`;
    }

    // Connected peers, one grid cell per screen edge
    this.renderPeers(peers);

    // Local focus badge: lit whenever control sits on this machine
    if (state.mode === "REMOTE_OUT") {
      this.localFocusBadge.classList.add("hidden");
    } else {
      this.localFocusBadge.classList.remove("hidden");
    }

    // PIN
    this.pinCode.innerText = state.pin || "------";
    this.chkAutoAccept.checked = !!state.auto_accept_lan;
    this.chkClipboardSync.checked = !!state.clipboard_sync;

    // Tuning values
    this.rangeEdgeThreshold.value = state.edge_threshold;
    this.valEdgeThreshold.innerText = `${state.edge_threshold} px`;
    this.rangeEdgeDelay.value = state.edge_delay_ms;
    this.valEdgeDelay.innerText = `${state.edge_delay_ms} ms`;

    // Stats
    this.statLatency.innerHTML = `${state.stats.latency_ms} <span class="stat-unit">ms</span>`;
    this.statPackets.innerText = (state.stats.packets_sent + state.stats.packets_recv).toLocaleString();
    this.statClipboard.innerText = state.stats.clipboard_count;
    this.statActiveMode.innerText = state.mode;

    // Discovered Peers List
    this.renderDiscoveredPeers(state.discovered_peers || [], peers);

    // File Transfers
    this.renderTransfers(state.transfers || []);
    if (state.receive_dir) {
      this.receiveDirLabel.innerText = `Saves to: ${state.receive_dir}`;
    }
  }

  renderPeers(peers) {
    this.peerCountBadge.innerText =
      `${peers.length} PC${peers.length === 1 ? "" : "s"} Paired`;

    // One slot per screen edge; anything without an edge falls through to the
    // overflow strip below the stage.
    const byEdge = {};
    const overflow = [];
    peers.forEach(p => {
      if (p.edge && this.slots[p.edge] && !byEdge[p.edge]) byEdge[p.edge] = p;
      else overflow.push(p);
    });

    Object.entries(this.slots).forEach(([edge, el]) => {
      const peer = byEdge[edge];
      el.innerHTML = "";
      if (peer) el.appendChild(this.peerCell(peer));
    });

    this.renderOverflow(overflow);
  }

  peerCell(peer) {
    const cell = document.createElement("div");
    cell.className = "peer-cell";
    cell.innerHTML = this.peerBoxHtml(peer);
    cell.appendChild(this.edgePicker(peer));
    return cell;
  }

  peerBoxHtml(peer) {
    // Mirrors the local monitor box so the arrangement reads as one desk
    return `
      <div class="monitor-box remote-monitor">
        <div class="monitor-top-bar">
          <span class="monitor-tag">${peer.edge ? peer.edge.toUpperCase() + " EDGE" : "NO SIDE"}</span>
          <span class="resolution-badge">${peer.width} × ${peer.height}</span>
        </div>
        <div class="monitor-body">
          <div class="monitor-pc-name">${this.escapeHtml(peer.name || "Remote PC")}</div>
          <div class="monitor-ip">${this.escapeHtml(peer.ip)}:${peer.port}</div>
          <div class="active-badge${peer.active ? "" : " hidden"}">● ACTIVE FOCUS</div>
        </div>
        <div class="monitor-stand"></div>
      </div>`;
  }

  edgePicker(peer) {
    // Lets you rearrange which side of THIS screen a PC sits on, overriding
    // auto-assignment. Picking a taken side swaps the two PCs.
    const bar = document.createElement("div");
    bar.className = "edge-picker";

    const label = document.createElement("span");
    label.className = "edge-picker-label";
    label.innerText = "Move off edge:";
    bar.appendChild(label);

    EDGE_BUTTONS.forEach(({ edge, glyph, title }) => {
      const b = document.createElement("button");
      b.className = "edge-btn" + (peer.edge === edge ? " active" : "");
      b.title = title;
      b.innerText = glyph;
      // dataset, not an HTML attribute - device_id comes off the LAN
      b.dataset.peerId = peer.device_id || "";
      b.dataset.edge = edge;
      bar.appendChild(b);
    });
    return bar;
  }

  renderOverflow(overflow) {
    if (!overflow.length) {
      this.overflowPeers.classList.add("hidden");
      this.overflowPeers.innerHTML = "";
      return;
    }
    this.overflowPeers.classList.remove("hidden");
    this.overflowPeers.innerHTML = `<div class="overflow-note">All four screen edges are taken.
      Give one of these a side (it swaps with whoever holds it) or press
      <strong>Switch To</strong> to hand it the cursor.</div>`;

    overflow.forEach(peer => {
      const cell = document.createElement("div");
      cell.className = "peer-cell";

      const btn = document.createElement("button");
      btn.className = "btn-secondary";
      btn.dataset.focusPeer = peer.device_id || "";
      // innerText instead of innerHTML: the name came off the LAN
      btn.innerText = `Switch To ${peer.name || "Remote PC"}${peer.active ? " ●" : ""}`;
      cell.appendChild(btn);
      cell.appendChild(this.edgePicker(peer));

      this.overflowPeers.appendChild(cell);
    });
  }

  renderTransfers(transfers) {
    if (!transfers.length) {
      this.noTransfers.style.display = "";
      this.transferList.querySelectorAll(".transfer-row").forEach(el => el.remove());
      return;
    }
    this.noTransfers.style.display = "none";
    const existingIds = new Set(transfers.map(t => t.id));
    this.transferList.querySelectorAll(".transfer-row").forEach(el => {
      if (!existingIds.has(el.dataset.tid)) el.remove();
    });

    transfers.slice(-8).forEach(t => {
      let row = this.transferList.querySelector(`[data-tid="${t.id}"]`);
      if (!row) {
        row = document.createElement("div");
        row.className = "transfer-row";
        row.dataset.tid = t.id;
        this.transferList.appendChild(row);
      }
      const pct = t.size ? Math.min(100, Math.round(t.done / t.size * 100)) : 0;
      const mbDone = (t.done / 1e6).toFixed(1);
      const mbSize = (t.size / 1e6).toFixed(1);
      const failed = t.status.startsWith("failed");
      const done = t.status === "complete";
      row.innerHTML = `
        <div style="display:flex; justify-content:space-between; font-size:13px; margin-bottom:3px;">
          <span>${t.dir === "send" ? "↑" : "↓"} ${this.escapeHtml(t.name)}</span>
          <span class="${failed ? "log-warning" : ""}">${failed ? "FAILED" : done ? "Complete" : pct + "%"}</span>
        </div>
        <div style="background:rgba(255,255,255,0.08); border-radius:4px; height:6px; overflow:hidden;">
          <div style="width:${pct}%; height:100%; background:${failed ? "#f87171" : done ? "#34d399" : "#22d3ee"}; transition:width 0.3s;"></div>
        </div>
        <div class="text-muted" style="font-size:11px; margin-top:2px;">${mbDone} / ${mbSize} MB</div>
      `;
    });
  }

  renderDiscoveredPeers(peers, connectedPeers) {
    this.discoveredCount.innerText = `${peers.length} Detected`;

    // A PC is "connected" if it sits anywhere in the peer table, not just if it
    // happens to be the one currently holding the cursor
    const connectedIds = new Set((connectedPeers || []).map(p => p.device_id));

    if (!peers.length) {
      this.emptyDiscovery.classList.remove("hidden");
      const items = this.discoveryList.querySelectorAll(".discovered-device-item");
      items.forEach(el => el.remove());
      return;
    }

    this.emptyDiscovery.classList.add("hidden");

    // Remove stale items
    const existingIds = new Set(peers.map(p => p.device_id));
    const domItems = this.discoveryList.querySelectorAll(".discovered-device-item");
    domItems.forEach(item => {
      if (!existingIds.has(item.dataset.deviceId)) {
        item.remove();
      }
    });

    // Upsert items
    peers.forEach(peer => {
      const isConnected = connectedIds.has(peer.device_id);
      let item = this.discoveryList.querySelector(`[data-device-id="${peer.device_id}"]`);

      if (!item) {
        item = document.createElement("div");
        item.className = "discovered-device-item";
        item.dataset.deviceId = peer.device_id;
        this.discoveryList.appendChild(item);
      }

      // name/ip/tcp_port arrive over the LAN from another machine - escape them
      item.innerHTML = `
        <div class="device-left">
          <div class="device-icon-box">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
              <line x1="8" y1="21" x2="16" y2="21"></line>
              <line x1="12" y1="17" x2="12" y2="21"></line>
            </svg>
          </div>
          <div class="device-info">
            <h4>${this.escapeHtml(peer.name || "Remote PC")}</h4>
            <div class="device-meta">${this.escapeHtml(peer.ip)}:${peer.tcp_port} • ${peer.width}×${peer.height}</div>
          </div>
        </div>
        <div class="device-actions">
          ${isConnected ? `
            <button class="btn-disconnect" data-action="disconnect_peer">Disconnect</button>
          ` : `
            <button class="btn-primary" data-action="connect">Connect</button>
          `}
        </div>
      `;

      // Attach button event. Values are read from the live `peer` object rather
      // than interpolated into data-* attributes, so nothing from the network
      // ever reaches an HTML attribute.
      const actionBtn = item.querySelector("button");
      if (actionBtn) {
        actionBtn.addEventListener("click", () => {
          if (actionBtn.dataset.action === "connect") {
            this.sendAction("connect", {
              ip: peer.ip,
              port: peer.tcp_port,
              pin: ""
            });
          } else {
            this.sendAction("disconnect_peer", { peer_id: peer.device_id });
          }
        });
      }
    });
  }

  addLogEntry(msg, level = "info", timeStr = null) {
    const time = timeStr || new Date().toLocaleTimeString();
    const entry = document.createElement("div");
    entry.className = `log-entry log-${level}`;
    entry.innerHTML = `<span class="log-time">${time}</span> ${this.escapeHtml(msg)}`;
    this.logStream.appendChild(entry);
    this.logStream.scrollTop = this.logStream.scrollHeight;
  }

  escapeHtml(text) {
    const div = document.createElement("div");
    div.innerText = text;
    return div.innerHTML;
  }
}

// Start client on page load
window.addEventListener("DOMContentLoaded", () => {
  window.shareLinkClient = new ShareLinkClient();
});
