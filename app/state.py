import time
import threading
import asyncio
import socket
from app.config import config
from app.layout import layout
from app.input_injector import injector
from app.clipboard_sync import clipboard
from app.input_capture import input_capture, MODE_LOCAL, MODE_REMOTE_OUT, MODE_REMOTE_IN
from app.discovery import discovery
from app.connection import conn_manager
from app.protocol import MSG_CLIPBOARD

class AppState:
    def __init__(self):
        self.ws_clients = set()
        self.ws_lock = threading.Lock()
        self.loop = None
        self.clipboard_sync_count = 0
        self.recent_logs = []
        self.log_lock = threading.Lock()

        # Connect hooks
        clipboard.on_change_callback = self._on_local_clipboard_changed
        conn_manager.set_input_capture(input_capture)
        conn_manager.on_connect_cb = self._on_peer_connected
        conn_manager.on_disconnect_cb = self._on_peer_disconnected
        input_capture.send_callback = self._send_network_packet
        input_capture.on_state_change = self._on_input_state_changed
        input_capture.is_connected_cb = conn_manager.is_connected
        discovery.on_peer_found = self._on_discovery_peer_found

        self.reconnect_thread = None
        self.auto_connect_lock = threading.Lock()

    def set_event_loop(self, loop):
        self.loop = loop

    def start_all(self):
        self.log("Starting ShareLink Engine...")
        clipboard.start()
        discovery.start()
        conn_manager.start_server()
        input_capture.start()

        self.reconnect_thread = threading.Thread(target=self._auto_reconnect_loop, daemon=True, name="AutoReconnect")
        self.reconnect_thread.start()

        self.log(f"Service running: {config.get('device_name')} ({discovery.get_local_ip()}:{config.get('tcp_port')})")

    def stop_all(self):
        self.log("Stopping ShareLink Engine...")
        input_capture.stop()
        conn_manager.stop()
        discovery.stop()
        clipboard.stop()

    def log(self, message: str, level: str = "info"):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "msg": message,
            "level": level
        }
        with self.log_lock:
            self.recent_logs.append(entry)
            if len(self.recent_logs) > 60:
                self.recent_logs.pop(0)
        self.broadcast_ws({"type": "log", "entry": entry})

    def _send_network_packet(self, pkt: dict, peer_id: str = ""):
        """Input is routed to exactly one peer. Never broadcast - that would
        type the same keystroke on every PC in the mesh."""
        if not peer_id:
            return False
        return conn_manager.send_to(peer_id, pkt)

    def _on_local_clipboard_changed(self, kind: str, data: str, text_hash: str):
        if not conn_manager.is_connected():
            return
        # Fan out to the whole mesh. Safe from echo loops: clipboard_sync guards
        # on a content hash, not per-peer, so a hash we originate is ignored coming back.
        sent = conn_manager.broadcast({
            "type": MSG_CLIPBOARD,
            "kind": kind,
            "data": data,
            "hash": text_hash
        })
        if sent:
            self.clipboard_sync_count += 1
            preview = data[:40] + ("..." if len(data) > 40 else "")
            self.log(f"Clipboard synced to {sent} PC(s): '{preview}'", "clipboard")
            self.broadcast_status()

    def _on_peer_connected(self, peer_info: dict):
        edge = peer_info.get("edge")
        where = f" on the {edge.upper()} edge" if edge else " (no free edge - use Switch To)"
        self.log(f"Connected to {peer_info.get('name')} ({peer_info.get('ip')}){where}", "success")
        self.broadcast_status()

    def _on_peer_disconnected(self, peer_info: dict):
        self.log(f"Disconnected from {peer_info.get('name')}", "warning")
        self.broadcast_status()

    def _on_input_state_changed(self, mode: str):
        self.broadcast_status()

    def _on_discovery_peer_found(self, peer_info: dict):
        self.log(f"Found PC nearby: {peer_info.get('name')} ({peer_info.get('ip')})", "info")
        self.broadcast_ws({"type": "peer_discovered", "peer": peer_info})

        # Automatic connection if auto_accept_lan is enabled and we aren't
        # already connected to this particular peer
        if config.get("auto_accept_lan", True) and not self._already_connected_to(peer_info.get("ip")):
            threading.Thread(
                target=self._auto_connect_to_peer,
                args=(peer_info,),
                daemon=True
            ).start()

    def _already_connected_to(self, ip: str) -> bool:
        if not ip:
            return False
        return any(p.get("ip") == ip for p in conn_manager.get_peers())

    def _should_initiate(self, peer_ip: str) -> bool:
        """Tie-break: only the lower-IP machine auto-initiates. Without this, both PCs
        auto-connect simultaneously and each accept loop disconnects the other's live socket."""
        if not peer_ip or peer_ip in ("127.0.0.1", "localhost"):
            return False
        my_ip = discovery.get_local_ip()
        if peer_ip == my_ip:
            return False
        try:
            return socket.inet_aton(my_ip) < socket.inet_aton(peer_ip)
        except OSError:
            return False

    def _auto_connect(self, ip, port, label) -> bool:
        if not ip:
            return False
        if self._already_connected_to(ip):
            return False
        if not self._should_initiate(ip):
            return False
        with self.auto_connect_lock:
            self.log(f"Auto-connecting to {label} ({ip}:{port})...", "info")
            success, msg = conn_manager.connect_to_peer(ip, int(port), config.get("pin", ""))
        if success:
            self.log(f"Successfully auto-connected to {label}!", "success")
        else:
            self.log(f"Auto-connect status: {msg}", "info")
        self.broadcast_status()
        return success

    def _auto_connect_to_peer(self, peer_info: dict):
        time.sleep(0.3)
        self._auto_connect(
            peer_info.get("ip"),
            peer_info.get("tcp_port", 42898),
            peer_info.get("name", "peer")
        )

    def _auto_reconnect_loop(self):
        """Continuously ensures connections to paired or discovered peers."""
        while True:
            time.sleep(3.0)
            if not config.get("auto_accept_lan", True):
                continue

            paired = config.get("paired_devices", {})
            my_ip = discovery.get_local_ip()
            for dev_id, info in paired.items():
                target_ip = info.get("ip")
                # Never auto-connect to self or loopback
                if not target_ip or target_ip == my_ip or target_ip in ("127.0.0.1", "localhost"):
                    continue
                # NOTE: the connect call used to sit *after* this continue, making it
                # unreachable - paired-device reconnect had never actually run.
                # No break: reconnect to every paired peer, not just the first.
                self._auto_connect(target_ip, info.get("port", 42898), info.get("name", target_ip))

    def emergency_breakout(self):
        """Forces input control back to local machine immediately."""
        self.log("Emergency Escape activated!", "warning")
        input_capture.emergency_reset()
        self.broadcast_status()

    def focus_peer(self, peer_id: str) -> bool:
        """Drives a specific peer directly. Used for peers past the 4-edge ceiling,
        which have no screen edge of their own to be reached by cursor."""
        if not conn_manager.is_connected(peer_id):
            return False
        # switch_to_remote only acts from MODE_LOCAL, so release whoever holds
        # control first - otherwise this is not an A -> B hop, just a no-op.
        if input_capture.mode != MODE_LOCAL:
            input_capture.emergency_reset()
        input_capture.switch_to_remote("right", 0.5, peer_id)
        return input_capture.active_peer_id == peer_id

    def get_status_payload(self) -> dict:
        peers = conn_manager.get_peers()
        active_peer_id = input_capture.active_peer_id
        active_latency = 0.0
        for p in peers:
            p["active"] = (p.get("device_id") == active_peer_id)
            if p["active"]:
                active_latency = p.get("latency_ms", 0.0)

        cx, cy = injector.get_cursor_pos()
        return {
            "device_id": config.get("device_id"),
            "device_name": config.get("device_name"),
            "local_ip": discovery.get_local_ip(),
            "tcp_port": config.get("tcp_port"),
            "pin": config.get("pin"),
            "connected": bool(peers),
            "peers": peers,
            "active_peer_id": active_peer_id,
            "mode": input_capture.mode,
            "edge_threshold": config.get("edge_threshold", 3),
            "edge_delay_ms": config.get("edge_delay_ms", 30),
            "auto_accept_lan": config.get("auto_accept_lan", True),
            "clipboard_sync": config.get("clipboard_sync", True),
            "local_screen": layout.get_screen_info(),
            "cursor": {"x": cx, "y": cy},
            "stats": {
                "packets_sent": input_capture.packets_sent,
                "packets_recv": conn_manager.totals()[1],
                "latency_ms": active_latency,
                "clipboard_count": self.clipboard_sync_count
            },
            "discovered_peers": discovery.get_peers(),
            "logs": self.recent_logs[-25:]
        }

    def register_ws(self, ws):
        with self.ws_lock:
            self.ws_clients.add(ws)

    def unregister_ws(self, ws):
        with self.ws_lock:
            self.ws_clients.discard(ws)

    def broadcast_status(self):
        self.broadcast_ws({"type": "status", "data": self.get_status_payload()})

    def broadcast_ws(self, msg: dict):
        if not self.loop:
            return
        with self.ws_lock:
            clients = list(self.ws_clients)
        if not clients:
            return

        async def _send_all():
            for ws in clients:
                try:
                    await ws.send_json(msg)
                except Exception:
                    pass

        try:
            asyncio.run_coroutine_threadsafe(_send_all(), self.loop)
        except Exception:
            pass

state = AppState()
