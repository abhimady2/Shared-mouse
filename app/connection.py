import socket
import select
import threading
import time
from app.config import config
from app.protocol import (
    MSG_HELLO, MSG_HELLO_ACK, MSG_PING, MSG_PONG,
    MSG_MOUSE_MOVE, MSG_MOUSE_BUTTON, MSG_MOUSE_WHEEL,
    MSG_KEY_EVENT, MSG_CLIPBOARD, MSG_SWITCH, MSG_RETURN,
    MSG_DISCONNECT, encode_message, decode_stream
)
from app.layout import layout
from app.input_injector import injector
from app.clipboard_sync import clipboard

# Screen edges, in the order peers get auto-assigned to them on connect.
EDGES = ["right", "left", "top", "bottom"]


def assign_edge(peer_id: str, saved_edge: str | None, taken: set) -> str | None:
    """Picks the edge a newly-connected peer owns.

    Reuses the peer's previous edge when still free, so a reconnect doesn't
    shuffle everyone's screen arrangement. Otherwise takes the first free edge.
    Returns None past the 4th peer - a rectangle has no 5th side.
    """
    if saved_edge in EDGES and saved_edge not in taken:
        return saved_edge
    for edge in EDGES:
        if edge not in taken:
            return edge
    return None


class PeerConn:
    """One live peer connection. Replaces the old single active_sock/active_peer_info pair."""

    def __init__(self, peer_id: str, sock: socket.socket, info: dict):
        self.peer_id = peer_id
        self.sock = sock
        self.info = info
        self.latency_ms = 0.0
        self.last_seen = time.time()
        self.bytes_sent = 0
        self.bytes_recv = 0
        self.write_lock = threading.Lock()

    @property
    def edge(self):
        return self.info.get("edge")


class PeerConnectionManager:
    def __init__(self, on_connect_cb=None, on_disconnect_cb=None, on_telemetry_cb=None,
                 device_id=None, tcp_port=None):
        self.on_connect_cb = on_connect_cb
        self.on_disconnect_cb = on_disconnect_cb
        self.on_telemetry_cb = on_telemetry_cb

        # Overridable so several instances can coexist (tests, and running
        # multiple copies of the app on one machine).
        self._device_id_override = device_id
        self._tcp_port_override = tcp_port

        self.server_sock = None
        self.running = False
        self.server_thread = None
        self.ping_thread = None

        # peer_id -> PeerConn
        self.peers = {}
        self.state_lock = threading.Lock()

        self.input_capture_ref = None

    def my_device_id(self) -> str:
        return self._device_id_override or config.get("device_id")

    def my_tcp_port(self) -> int:
        return int(self._tcp_port_override or config.get("tcp_port", 42898))

    def set_input_capture(self, input_capture):
        self.input_capture_ref = input_capture

    def start_server(self):
        if self.running:
            return
        self.running = True

        port = int(self._tcp_port_override or config.get("tcp_port", 42898))
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", port))
        self.server_sock.listen(8)

        self.server_thread = threading.Thread(target=self._accept_loop, daemon=True, name="TCPAccept")
        self.server_thread.start()

        self.ping_thread = threading.Thread(target=self._ping_loop, daemon=True, name="TCPPing")
        self.ping_thread.start()

    def stop(self):
        self.running = False
        self.disconnect()
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass

    # --- peer table ---

    def is_connected(self, peer_id: str | None = None) -> bool:
        with self.state_lock:
            if peer_id is None:
                return bool(self.peers)
            return peer_id in self.peers

    def get_peers(self) -> list:
        with self.state_lock:
            return [self._snapshot(c) for c in self.peers.values()]

    def get_peer_info(self, peer_id: str | None = None):
        """Single peer snapshot. Defaults to an arbitrary peer so single-peer
        callers (file transfer) keep working unchanged."""
        with self.state_lock:
            conn = self.peers.get(peer_id) if peer_id is not None else next(iter(self.peers.values()), None)
            return self._snapshot(conn) if conn else None

    @staticmethod
    def _snapshot(conn: "PeerConn") -> dict:
        info = dict(conn.info)
        info["latency_ms"] = round(conn.latency_ms, 1)
        return info

    def peer_for_edge(self, edge: str):
        with self.state_lock:
            for peer_id, conn in self.peers.items():
                if conn.edge == edge:
                    return peer_id
        return None

    def assigned_edges(self) -> set:
        with self.state_lock:
            return {c.edge for c in self.peers.values() if c.edge}

    def set_edge(self, peer_id: str, edge: str) -> bool:
        """Manually re-assigns a peer's screen edge, overriding auto-assignment.

        If another peer already holds that edge the two swap, so an edge never
        maps to two peers - routing asks peer_for_edge() and would otherwise
        pick whichever happened to come first in the dict.
        """
        if edge not in EDGES:
            return False
        with self.state_lock:
            conn = self.peers.get(peer_id)
            if not conn:
                return False
            old_edge = conn.edge
            if old_edge == edge:
                return True
            other = next((c for c in self.peers.values()
                          if c.edge == edge and c.peer_id != peer_id), None)
            conn.info["edge"] = edge
            if other:
                other.info["edge"] = old_edge

        config.set_peer_edge(peer_id, edge)
        if other:
            config.set_peer_edge(other.peer_id, old_edge)
        return True

    def totals(self):
        with self.state_lock:
            return (sum(c.bytes_sent for c in self.peers.values()),
                    sum(c.bytes_recv for c in self.peers.values()))

    def _register_peer(self, peer_id: str, sock: socket.socket, info: dict):
        """Adds a peer to the table, assigning it a screen edge. Never evicts other peers."""
        taken = self.assigned_edges()
        saved = config.get("paired_devices", {}).get(peer_id, {}).get("edge")
        info["edge"] = assign_edge(peer_id, saved, taken)

        conn = PeerConn(peer_id, sock, info)
        with self.state_lock:
            old = self.peers.get(peer_id)
            self.peers[peer_id] = conn
        # Same peer_id reconnecting (network blip) - drop the stale socket
        if old:
            self._close_conn(old)

        config.add_paired_device(peer_id, info["name"], info["ip"], info["port"])
        if info["edge"]:
            config.set_peer_edge(peer_id, info["edge"])

    # --- sending ---

    def send_to(self, peer_id: str, msg_dict: dict) -> bool:
        with self.state_lock:
            conn = self.peers.get(peer_id)
        if not conn:
            return False
        return self._send_conn(conn, msg_dict)

    def broadcast(self, msg_dict: dict) -> int:
        """Sends to every connected peer. Returns how many accepted it."""
        with self.state_lock:
            conns = list(self.peers.values())
        return sum(1 for c in conns if self._send_conn(c, msg_dict))

    def _send_conn(self, conn: "PeerConn", msg_dict: dict) -> bool:
        try:
            data = encode_message(msg_dict)
            with conn.write_lock:
                conn.sock.sendall(data)
            conn.bytes_sent += len(data)
            return True
        except Exception as e:
            print(f"[Connection] Send error to {conn.peer_id}: {e}")
            self.disconnect(conn.peer_id)
            return False

    # --- connecting ---

    def connect_to_peer(self, ip: str, port: int, pin: str = None):
        """Initiates an outgoing connection. Existing peers are left untouched."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(5.0)
            s.connect((ip, int(port)))
            s.settimeout(None)

            hello = {
                "type": MSG_HELLO,
                "device_id": self.my_device_id(),
                "name": config.get("device_name"),
                "pin": pin or config.get("pin"),
                # Our *listen* port. An acceptor only ever sees the ephemeral
                # source port, and saving that as the peer's address left that
                # side unable to ever reconnect.
                "port": self.my_tcp_port(),
                "width": layout.local_width,
                "height": layout.local_height
            }
            s.sendall(encode_message(hello))

            s.settimeout(4.0)
            raw_ack = s.recv(1024)
            s.settimeout(None)
            buffer = bytearray(raw_ack)
            acks = decode_stream(buffer)

            if not acks or acks[0].get("type") != MSG_HELLO_ACK or acks[0].get("status") != "OK":
                s.close()
                return False, "Handshake rejected or invalid PIN"

            ack = acks[0]
            peer_id = ack.get("device_id")
            if not peer_id:
                s.close()
                return False, "Peer did not identify itself"

            info = {
                "device_id": peer_id,
                "name": ack.get("name"),
                "ip": ip,
                "port": int(port),
                "width": ack.get("width", 1920),
                "height": ack.get("height", 1080),
            }

            self._register_peer(peer_id, s, info)
            self._start_reader(s, peer_id)

            if self.on_connect_cb:
                self.on_connect_cb(self.get_peer_info(peer_id))

            return True, "Connected successfully"

        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def _accept_loop(self):
        while self.running:
            try:
                client_sock, addr = self.server_sock.accept()
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                client_sock.settimeout(4.0)
                raw = client_sock.recv(1024)
                client_sock.settimeout(None)

                buf = bytearray(raw)
                msgs = decode_stream(buf)
                if not msgs or msgs[0].get("type") != MSG_HELLO:
                    client_sock.close()
                    continue

                hello = msgs[0]
                peer_id = hello.get("device_id")

                # File-transfer connections are routed to the transfer manager
                if hello.get("file_transfer"):
                    from app.file_transfer import file_transfer
                    file_transfer.accept_transfer(client_sock, hello, addr)
                    continue

                # Reject self-connections (stale 127.0.0.1 pairings / own probes)
                if not peer_id or peer_id == self.my_device_id():
                    client_sock.close()
                    continue

                peer_pin = hello.get("pin", "")
                auto_accept = config.get("auto_accept_lan", True)
                my_pin = config.get("pin")
                is_paired = peer_id in config.get("paired_devices", {})

                if not (auto_accept or is_paired or peer_pin == my_pin):
                    client_sock.sendall(encode_message({
                        "type": MSG_HELLO_ACK,
                        "status": "DENIED",
                        "reason": "Invalid PIN"
                    }))
                    client_sock.close()
                    continue

                ack = {
                    "type": MSG_HELLO_ACK,
                    "status": "OK",
                    "device_id": self.my_device_id(),
                    "name": config.get("device_name"),
                    "width": layout.local_width,
                    "height": layout.local_height
                }
                client_sock.sendall(encode_message(ack))

                info = {
                    "device_id": peer_id,
                    "name": hello.get("name", "Remote PC"),
                    "ip": addr[0],
                    # The peer's advertised listen port; addr[1] is ephemeral and
                    # would make outbound reconnect to this peer impossible.
                    "port": int(hello.get("port") or config.get("tcp_port", 42898)),
                    "width": hello.get("width", 1920),
                    "height": hello.get("height", 1080),
                }

                # ponytail: this used to call self.disconnect() here, which evicted the
                # existing peer whenever a third PC joined the mesh.
                self._register_peer(peer_id, client_sock, info)
                self._start_reader(client_sock, peer_id)

                if self.on_connect_cb:
                    self.on_connect_cb(self.get_peer_info(peer_id))

            except Exception:
                pass

    # --- teardown ---

    @staticmethod
    def _close_conn(conn: "PeerConn"):
        try:
            conn.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            conn.sock.close()
        except Exception:
            pass

    def disconnect(self, peer_id: str | None = None):
        """Closes one peer (or all when peer_id is None) and reports it."""
        if peer_id is None:
            with self.state_lock:
                ids = list(self.peers)
            for pid in ids:
                self.disconnect(pid)
            return

        with self.state_lock:
            conn = self.peers.pop(peer_id, None)

        if not conn:
            return

        try:
            conn.sock.sendall(encode_message({"type": MSG_DISCONNECT}))
        except Exception:
            pass

        if self.input_capture_ref:
            # Lets capture exit remote mode only if it was driving THIS peer
            self.input_capture_ref.on_peer_disconnected(peer_id)

        self._close_conn(conn)

        if self.on_disconnect_cb:
            self.on_disconnect_cb(self._snapshot(conn))

    def _start_reader(self, sock: socket.socket, peer_id: str):
        threading.Thread(
            target=self._reader_loop,
            args=(sock, peer_id),
            daemon=True,
            name=f"TCPReader-{peer_id}"
        ).start()

    def _reader_loop(self, sock: socket.socket, peer_id: str):
        buffer = bytearray()
        while self.running:
            try:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                with self.state_lock:
                    conn = self.peers.get(peer_id)
                if conn:
                    conn.bytes_recv += len(chunk)
                    conn.last_seen = time.time()
                buffer.extend(chunk)

                for msg in decode_stream(buffer):
                    # Scoped per message: a bug in one handler must not tear the
                    # link down. Without this, any handler error breaks the read
                    # loop below and looks like the peer randomly disconnecting.
                    try:
                        self._dispatch_message(msg, peer_id)
                    except Exception as e:
                        print(f"[Connection] Handler error on {msg.get('type')} "
                              f"from {peer_id}: {e}")

            except Exception:
                break

        with self.state_lock:
            current = self.peers.get(peer_id)
        # Only tear down if this socket is still the live one for that peer
        if current and current.sock is sock:
            self.disconnect(peer_id)

    def _dispatch_message(self, msg: dict, peer_id: str):
        mtype = msg.get("type")

        if mtype == MSG_PING:
            self.send_to(peer_id, {"type": MSG_PONG, "ts": msg.get("ts", time.time())})

        elif mtype == MSG_PONG:
            with self.state_lock:
                conn = self.peers.get(peer_id)
                if conn:
                    conn.latency_ms = (time.time() - msg.get("ts", time.time())) * 1000.0

        elif mtype == MSG_MOUSE_MOVE:
            if self.input_capture_ref:
                self.input_capture_ref.last_remote_activity = time.time()
            injector.move_mouse_rel(msg.get("dx", 0), msg.get("dy", 0))

        elif mtype == MSG_MOUSE_BUTTON:
            if self.input_capture_ref:
                self.input_capture_ref.last_remote_activity = time.time()
            injector.mouse_button(msg.get("btn", "left"), msg.get("action", "down"))

        elif mtype == MSG_MOUSE_WHEEL:
            if self.input_capture_ref:
                self.input_capture_ref.last_remote_activity = time.time()
            injector.mouse_wheel(msg.get("delta", 0), msg.get("horizontal", False))

        elif mtype == MSG_KEY_EVENT:
            if self.input_capture_ref:
                self.input_capture_ref.last_remote_activity = time.time()
            injector.key_event(msg.get("vk", 0), action=msg.get("action", "down"))

        elif mtype == MSG_CLIPBOARD:
            clipboard.set_remote_clipboard(
                msg.get("kind", "text"),
                msg.get("data", ""),
                msg.get("hash", "")
            )

        elif mtype == MSG_SWITCH:
            if self.input_capture_ref:
                self.input_capture_ref.handle_remote_switch(
                    msg.get("edge", "right"),
                    msg.get("entry_pct", 0.5),
                    peer_id
                )

        elif mtype == MSG_RETURN:
            if self.input_capture_ref:
                self.input_capture_ref.handle_remote_return(msg.get("entry_pct", 0.5), peer_id)

        elif mtype == MSG_DISCONNECT:
            self.disconnect(peer_id)

    def _ping_loop(self):
        """Per-peer heartbeat: measures latency and drops only the peer that went quiet."""
        while self.running:
            time.sleep(1.5)
            now = time.time()
            with self.state_lock:
                conns = list(self.peers.values())

            for conn in conns:
                if now - conn.last_seen > 4.5:
                    print(f"[Connection] Heartbeat timeout for {conn.peer_id}. Disconnecting.")
                    self.disconnect(conn.peer_id)
                else:
                    self.send_to(conn.peer_id, {"type": MSG_PING, "ts": now})


conn_manager = PeerConnectionManager()
