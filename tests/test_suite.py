import sys
import time
import socket
import json
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.protocol import (
    encode_message, decode_stream,
    MSG_HELLO, MSG_HELLO_ACK, MSG_PING, MSG_PONG,
    MSG_MOUSE_MOVE, MSG_KEY_EVENT, MSG_CLIPBOARD, MSG_SWITCH, MSG_RETURN
)
from app.layout import ScreenLayout
from app.input_injector import InputInjector, INPUT
import ctypes

class TestShareLinkProtocol(unittest.TestCase):
    def test_encode_decode(self):
        msg1 = {"type": MSG_MOUSE_MOVE, "dx": 15, "dy": -8}
        msg2 = {"type": MSG_KEY_EVENT, "vk": 65, "action": "down"}

        encoded = encode_message(msg1) + encode_message(msg2)
        buf = bytearray(encoded)

        decoded = decode_stream(buf)
        self.assertEqual(len(decoded), 2)
        self.assertEqual(decoded[0]["type"], MSG_MOUSE_MOVE)
        self.assertEqual(decoded[0]["dx"], 15)
        self.assertEqual(decoded[0]["dy"], -8)
        self.assertEqual(decoded[1]["vk"], 65)
        self.assertEqual(len(buf), 0)

    def test_partial_stream(self):
        msg = {"type": MSG_PING, "ts": 123456.789}
        encoded = encode_message(msg)
        
        # Split in half
        half1 = encoded[:len(encoded)//2]
        half2 = encoded[len(encoded)//2:]

        buf = bytearray(half1)
        decoded1 = decode_stream(buf)
        self.assertEqual(len(decoded1), 0) # incomplete

        buf.extend(half2)
        decoded2 = decode_stream(buf)
        self.assertEqual(len(decoded2), 1)
        self.assertEqual(decoded2[0]["ts"], 123456.789)


class TestInputStructures(unittest.TestCase):
    def test_win32_input_size(self):
        # On 64-bit Windows, INPUT struct must be exactly 40 bytes
        self.assertEqual(ctypes.sizeof(INPUT), 40)

    def test_injector_cursor_pos(self):
        injector = InputInjector()
        x, y = injector.get_cursor_pos()
        self.assertIsInstance(x, int)
        self.assertIsInstance(y, int)


class TestLayoutMath(unittest.TestCase):
    def setUp(self):
        self.layout = ScreenLayout()
        self.layout.local_width = 1920
        self.layout.local_height = 1080

    def test_edge_crossing_right(self):
        # Cursor at right border
        crossed, edge, pct = self.layout.check_edge_crossing(1920, 540, "right")
        # May be false initially due to delay_ms, let's sleep past delay
        time.sleep(0.05)
        crossed, edge, pct = self.layout.check_edge_crossing(1920, 540, "right")
        self.assertTrue(crossed)
        self.assertEqual(edge, "right")
        self.assertAlmostEqual(pct, 0.5, places=1)

    def test_resolution_mapping(self):
        # 1080p host to 4K remote (3840x2160) with 40px safety margin
        rx, ry = self.layout.calculate_remote_entry("right", 0.5, 3840, 2160)
        self.assertEqual(rx, 40) # enters 40px safely onto remote
        self.assertEqual(ry, 1080) # 50% of 2160

    def test_zero_zero_guard(self):
        # Origin (0, 0) must NEVER trigger edge crossing on left or top
        crossed_left, _, _ = self.layout.check_edge_crossing(0, 0, "left")
        self.assertFalse(crossed_left)
        crossed_top, _, _ = self.layout.check_edge_crossing(0, 0, "top")
        self.assertFalse(crossed_top)

    def test_return_edge(self):
        # Host switched on "right", so remote return edge is "left" (x <= 2)
        # First hit sets timer
        self.layout.check_return_edge(1, 540, "right", 1920, 1080)
        time.sleep(0.07) # Wait past 60ms intentional hold
        hit, pct = self.layout.check_return_edge(1, 540, "right", 1920, 1080)
        self.assertTrue(hit)
        self.assertAlmostEqual(pct, 0.5, places=1)


class TestNetworkSimulation(unittest.TestCase):
    def test_tcp_handshake(self):
        test_port = 52199
        # Server socket
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("127.0.0.1", test_port))
        server_sock.listen(1)

        # Client socket
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_sock.connect(("127.0.0.1", test_port))

        conn, addr = server_sock.accept()

        # Send HELLO
        hello = {
            "type": MSG_HELLO,
            "device_id": "test_cli",
            "name": "Test Client",
            "pin": "123456",
            "width": 1920,
            "height": 1080
        }
        client_sock.sendall(encode_message(hello))

        # Server receives and responds
        raw = conn.recv(1024)
        msgs = decode_stream(bytearray(raw))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["type"], MSG_HELLO)

        ack = {
            "type": MSG_HELLO_ACK,
            "status": "OK",
            "device_id": "test_srv",
            "name": "Test Server",
            "width": 1920,
            "height": 1080
        }
        conn.sendall(encode_message(ack))

        # Client reads ACK
        raw_ack = client_sock.recv(1024)
        acks = decode_stream(bytearray(raw_ack))
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0]["status"], "OK")

        client_sock.close()
        conn.close()
        server_sock.close()


class TestDiscoveryArpCache(unittest.TestCase):
    """Regression: arp.exe was spawned per broadcast/probe tick, flashing a
    console window every ~1.2s in the console-less frozen exe."""

    def test_arp_is_cached_not_respawned(self):
        import subprocess
        import app.discovery as disc

        spawns = []
        real = subprocess.check_output
        subprocess.check_output = lambda *a, **k: (spawns.append(1), real(*a, **k))[1]
        try:
            disc._arp_cache["t"] = 0.0
            for _ in range(25):
                disc.discovery._get_discovery_targets()
        finally:
            subprocess.check_output = real

        self.assertEqual(len(spawns), 1, f"arp spawned {len(spawns)}x, cache not holding")

    def test_targets_always_include_broadcast(self):
        import app.discovery as disc
        targets = disc.discovery._get_discovery_targets()
        self.assertIn("255.255.255.255", targets)


class TestEdgeAssignment(unittest.TestCase):
    """Auto-assignment of connected peers to screen edges."""

    def setUp(self):
        from app.connection import assign_edge, EDGES
        self.assign_edge = assign_edge
        self.EDGES = EDGES

    def test_fills_edges_in_order(self):
        taken = set()
        order = []
        for i in range(4):
            edge = self.assign_edge(f"peer{i}", None, taken)
            order.append(edge)
            taken.add(edge)
        self.assertEqual(order, ["right", "left", "top", "bottom"])

    def test_fifth_peer_has_no_edge(self):
        # A rectangle has four sides - this is arithmetic, not a tuning limit
        taken = set(self.EDGES)
        self.assertIsNone(self.assign_edge("peer5", None, taken))

    def test_reuses_saved_edge_when_free(self):
        # A reconnect must not reshuffle everyone's screen arrangement
        self.assertEqual(self.assign_edge("peerA", "top", {"right"}), "top")

    def test_saved_edge_yields_if_taken(self):
        self.assertEqual(self.assign_edge("peerA", "right", {"right"}), "left")

    def test_ignores_bogus_saved_edge(self):
        self.assertEqual(self.assign_edge("peerA", "diagonal", {"right"}), "left")


class TestMultiPeerMesh(unittest.TestCase):
    """Regression: a 3rd PC connecting used to evict the 2nd.

    _accept_loop called self.disconnect() on every accepted connection, so the
    mesh silently degraded to a pair. This test would have caught that.
    """

    BASE_PORT = 43910

    def setUp(self):
        from app.config import config
        self.config = config
        self._saved_save = config.save
        self._saved_cfg = dict(config.config)
        config.save = lambda: None      # never touch the real config.json

    def tearDown(self):
        self.config.save = self._saved_save
        self.config.config.clear()
        self.config.config.update(self._saved_cfg)
        for m in getattr(self, "mgrs", []):
            m.stop()

    def _wait_for(self, cond, timeout=4.0):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def _build_mesh(self, n=3):
        import app.connection as cm
        self.mgrs = []
        for i in range(n):
            m = cm.PeerConnectionManager(
                device_id=f"testpeer{i}", tcp_port=self.BASE_PORT + i)
            m.start_server()
            self.mgrs.append(m)
        # Hub topology: every other node dials node 0, so node 0 is the one that
        # *accepts* multiple connections. That is the shape that exposed the bug -
        # a star where node 0 initiated everything would never hit _accept_loop twice.
        for i in range(1, n):
            ok, msg = self.mgrs[i].connect_to_peer(
                "127.0.0.1", self.BASE_PORT, self.config.get("pin", ""))
            self.assertTrue(ok, f"node{i} could not reach node0: {msg}")
        return self.mgrs

    def test_three_peers_connected_simultaneously(self):
        mgrs = self._build_mesh(3)

        self.assertTrue(
            self._wait_for(lambda: len(mgrs[0].peers) == 2),
            f"node0 should hold 2 peers, got {len(mgrs[0].peers)}")
        # The eviction bug showed up here: node1's link to node0 got dropped
        # as soon as node2 joined.
        for i in (1, 2):
            self.assertTrue(
                self._wait_for(lambda i=i: len(mgrs[i].peers) == 1),
                f"node{i} lost its connection")

    def test_each_peer_gets_a_distinct_edge(self):
        mgrs = self._build_mesh(3)
        self.assertTrue(self._wait_for(lambda: len(mgrs[0].assigned_edges()) == 2),
                        "expected 2 distinct edges")
        edges = mgrs[0].assigned_edges()
        self.assertEqual(len(edges), 2)
        self.assertTrue(edges <= {"right", "left", "top", "bottom"})

    def test_disconnecting_one_peer_leaves_the_other(self):
        mgrs = self._build_mesh(3)
        self.assertTrue(self._wait_for(lambda: len(mgrs[0].peers) == 2))

        victim = next(iter(mgrs[0].peers))
        mgrs[0].disconnect(victim)

        self.assertEqual(len(mgrs[0].peers), 1)
        self.assertTrue(mgrs[0].is_connected(), "a peer should still be connected")

    def test_input_routes_to_one_peer_only(self):
        """Input must never broadcast - that would type on every PC at once."""
        mgrs = self._build_mesh(3)
        self.assertTrue(self._wait_for(lambda: len(mgrs[0].peers) == 2))

        target = next(iter(mgrs[0].peers))
        self.assertTrue(mgrs[0].send_to(target, {"type": "noop"}))
        self.assertFalse(mgrs[0].send_to("does-not-exist", {"type": "noop"}))


class TestDashboardPayload(unittest.TestCase):
    """The dashboard renders whatever get_status_payload() hands it.

    app.js was rewritten around state.peers / active_peer_id; this pins that
    contract against the app's real conn_manager, so a rename in state.py or a
    field the JS reads going missing fails here instead of in the browser.
    """

    BASE_PORT = 43930

    def setUp(self):
        from app.config import config
        from app import connection as cm
        self.config = config
        self.cm = cm
        self._saved_save = config.save
        self._saved_cfg = dict(config.config)
        config.save = lambda: None     # never touch the real config.json
        config.config["device_id"] = "dash-host"
        config.config["device_name"] = "Dash Host"
        self.clients = []

    def tearDown(self):
        self.cm.conn_manager.stop()
        for c in self.clients:
            c.stop()
        self.config.save = self._saved_save
        self.config.config.clear()
        self.config.config.update(self._saved_cfg)

    def test_payload_carries_every_peer_and_its_edge(self):
        from app.state import state
        from app.connection import EDGES

        host = self.cm.conn_manager
        host._device_id_override = "dash-host"
        host._tcp_port_override = self.BASE_PORT
        host.start_server()

        for i in (1, 2, 3):
            c = self.cm.PeerConnectionManager(
                device_id=f"dash-peer{i}", tcp_port=self.BASE_PORT + i)
            c.start_server()
            ok, msg = c.connect_to_peer(
                "127.0.0.1", self.BASE_PORT, self.config.get("pin", ""))
            self.assertTrue(ok, msg)
            self.clients.append(c)

        end = time.time() + 4.0
        while time.time() < end and len(host.peers) < 3:
            time.sleep(0.02)

        payload = state.get_status_payload()
        peers = payload["peers"]

        self.assertTrue(payload["connected"])
        self.assertEqual(len(peers), 3, f"dashboard would show {len(peers)} of 3 PCs")
        self.assertEqual(payload["active_peer_id"], "")

        # Every field app.js reads off a peer box
        for p in peers:
            for key in ("device_id", "name", "ip", "port", "width", "height",
                        "edge", "active", "latency_ms"):
                self.assertIn(key, p, f"peer box needs {key}")

        edges = [p["edge"] for p in peers]
        self.assertEqual(len(set(edges)), 3, f"edges must be distinct, got {edges}")
        self.assertTrue(set(edges) <= set(EDGES), edges)
        self.assertEqual(sum(1 for p in peers if p["active"]), 0)


class _FakeCapture:
    """Stands in for input_capture so the input path can be tested without
    installing real suppress=True hooks (which would hijack the user's mouse).

    The signatures here are deliberately the real ones - a *args catch-all would
    hide an arity mismatch at the call site and defeat the point.
    """

    def __init__(self, raise_on_switch=False):
        self.calls = []
        self.raise_on_switch = raise_on_switch
        self.disconnected = []
        self.last_remote_activity = 0.0

    def handle_remote_switch(self, edge, entry_pct, peer_id=""):
        if self.raise_on_switch:
            raise RuntimeError("handler blew up")
        self.calls.append(("switch", edge, entry_pct, peer_id))

    def handle_remote_return(self, entry_pct, peer_id=""):
        self.calls.append(("return", entry_pct, peer_id))

    def on_peer_disconnected(self, peer_id):
        self.disconnected.append(peer_id)


class TestRemoteControlDispatch(unittest.TestCase):
    """Regression: MSG_SWITCH was dispatched with two extra positional args
    (local width/height) long after the signature dropped them. Every edge
    crossing therefore raised TypeError, and _reader_loop's blanket
    `except Exception: break` swallowed it and disconnected the peer - so the
    app dropped the link every time the cursor crossed a screen edge.
    """

    BASE_PORT = 43950

    def setUp(self):
        from app.config import config
        self.config = config
        self._saved_save = config.save
        self._saved_cfg = dict(config.config)
        config.save = lambda: None
        self.mgrs = []

    def tearDown(self):
        for m in self.mgrs:
            m.stop()
        self.config.save = self._saved_save
        self.config.config.clear()
        self.config.config.update(self._saved_cfg)

    def _wait_for(self, cond, timeout=4.0):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def _pair(self, capture):
        import app.connection as cm
        host = cm.PeerConnectionManager(device_id="rc-host", tcp_port=self.BASE_PORT)
        host.start_server()
        host.set_input_capture(capture)
        client = cm.PeerConnectionManager(device_id="rc-peer", tcp_port=self.BASE_PORT + 1)
        client.start_server()
        self.mgrs += [host, client]
        ok, msg = client.connect_to_peer(
            "127.0.0.1", self.BASE_PORT, self.config.get("pin", ""))
        self.assertTrue(ok, msg)
        self.assertTrue(self._wait_for(lambda: host.is_connected("rc-peer")),
                        "host never registered the peer")
        return host, client

    def test_switch_reaches_capture_with_the_routing_peer_id(self):
        cap = _FakeCapture()
        host, client = self._pair(cap)

        client.send_to("rc-host", {"type": MSG_SWITCH, "edge": "left", "entry_pct": 0.25})

        self.assertTrue(self._wait_for(lambda: cap.calls),
                        "handle_remote_switch was never called")
        self.assertEqual(cap.calls[0], ("switch", "left", 0.25, "rc-peer"))

        # The link has to survive the switch - it never used to
        time.sleep(0.3)
        self.assertTrue(host.is_connected("rc-peer"),
                        "crossing an edge disconnected the peer")

    def test_a_raising_handler_does_not_tear_down_the_link(self):
        cap = _FakeCapture(raise_on_switch=True)
        host, client = self._pair(cap)

        client.send_to("rc-host", {"type": MSG_SWITCH, "edge": "left", "entry_pct": 0.5})
        time.sleep(0.4)

        self.assertTrue(host.is_connected("rc-peer"),
                        "one bad message dropped the whole connection")
        self.assertTrue(host.send_to("rc-peer", {"type": "noop"}),
                        "link stopped carrying traffic after a handler error")

    def test_return_message_is_delivered_too(self):
        cap = _FakeCapture()
        host, client = self._pair(cap)

        client.send_to("rc-host", {"type": MSG_RETURN, "entry_pct": 0.75})

        self.assertTrue(self._wait_for(lambda: cap.calls))
        self.assertEqual(cap.calls[0], ("return", 0.75, "rc-peer"))

    def test_accepted_peer_records_listen_port_not_ephemeral_one(self):
        """An acceptor only sees the peer's ephemeral source port. Saving that
        as the peer's address made outbound reconnect dial a dead port, so the
        side that was dialled could never re-establish the link."""
        cap = _FakeCapture()
        host, _ = self._pair(cap)

        info = host.get_peer_info("rc-peer")
        self.assertEqual(info["port"], self.BASE_PORT + 1)


class TestManualEdgeAssignment(unittest.TestCase):
    """The dashboard's per-peer "move off edge" picker."""

    BASE_PORT = 43970

    def setUp(self):
        from app.config import config
        self.config = config
        self._saved_save = config.save
        self._saved_cfg = dict(config.config)
        config.save = lambda: None
        self.mgrs = []

    def tearDown(self):
        for m in self.mgrs:
            m.stop()
        self.config.save = self._saved_save
        self.config.config.clear()
        self.config.config.update(self._saved_cfg)

    def _wait_for(self, cond, timeout=4.0):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def _hub(self, n=3):
        import app.connection as cm
        host = cm.PeerConnectionManager(device_id="edge-host", tcp_port=self.BASE_PORT)
        host.start_server()
        self.mgrs.append(host)
        for i in range(1, n):
            c = cm.PeerConnectionManager(
                device_id=f"edge-p{i}", tcp_port=self.BASE_PORT + i)
            c.start_server()
            self.mgrs.append(c)
            ok, msg = c.connect_to_peer(
                "127.0.0.1", self.BASE_PORT, self.config.get("pin", ""))
            self.assertTrue(ok, msg)
        self.assertTrue(self._wait_for(lambda: len(host.peers) == n - 1),
                        f"host holds {len(host.peers)} of {n - 1} peers")
        return host

    def test_moves_a_peer_to_a_chosen_side(self):
        host = self._hub(2)
        peer = host.get_peers()[0]
        free = next(e for e in ("right", "left", "top", "bottom") if e != peer["edge"])

        self.assertTrue(host.set_edge(peer["device_id"], free))
        self.assertEqual(host.peer_for_edge(free), peer["device_id"])

    def test_moving_onto_a_taken_side_swaps_the_two(self):
        # host holds 3 peers here, so a swap must leave the uninvolved one alone
        host = self._hub(4)
        a, b, c = host.get_peers()
        a_edge, b_edge = a["edge"], b["edge"]
        self.assertNotEqual(a_edge, b_edge)

        self.assertTrue(host.set_edge(a["device_id"], b_edge))

        # Both keep a side, and the sides trade places
        self.assertEqual(host.peer_for_edge(b_edge), a["device_id"])
        self.assertEqual(host.peer_for_edge(a_edge), b["device_id"])
        # Three peers, three distinct sides, nobody dropped
        self.assertEqual(len(host.assigned_edges()), 3)
        self.assertEqual(host.peer_for_edge(c["edge"]), c["device_id"])

    def test_rejects_an_unknown_side_or_peer(self):
        host = self._hub(2)
        pid = host.get_peers()[0]["device_id"]

        self.assertFalse(host.set_edge(pid, "diagonal"))
        self.assertFalse(host.set_edge("not-a-peer", "right"))
        # Nothing moved
        self.assertTrue(host.get_peers()[0]["edge"] in ("right", "left", "top", "bottom"))

    def test_reassigning_to_the_same_side_is_a_noop_success(self):
        host = self._hub(2)
        peer = host.get_peers()[0]
        self.assertTrue(host.set_edge(peer["device_id"], peer["edge"]))
        self.assertEqual(host.peer_for_edge(peer["edge"]), peer["device_id"])


class TestOutboundQueueOrdering(unittest.TestCase):
    """Accumulated mouse motion must be flushed ahead of a click, or the remote
    cursor is not where the user expects when the click lands."""

    def test_pending_move_is_queued_before_click(self):
        from app.input_capture import InputCaptureManager
        ic = InputCaptureManager()
        with ic._move_acc_lock:
            ic._pend_dx, ic._pend_dy = 5, -3
        ic._enqueue({"type": "MB", "btn": "left", "action": "down"}, "peer")
        # _enqueue only queues; _send_loop drains it, so assert on the queue
        first = ic._send_q.get_nowait()
        second = ic._send_q.get_nowait()
        self.assertEqual(first[0]["type"], "MM")
        self.assertEqual(first[0]["dx"], 5)
        self.assertEqual(second[0]["type"], "MB")
        # Accumulator is drained
        self.assertEqual(ic._take_pending_move(), (0, 0))

    def test_moves_coalesce_into_one_packet(self):
        from app.input_capture import InputCaptureManager
        ic = InputCaptureManager()
        with ic._move_acc_lock:
            ic._pend_dx += 2
            ic._pend_dy += 1
        with ic._move_acc_lock:
            ic._pend_dx += 3
            ic._pend_dy += 4
        self.assertEqual(ic._take_pending_move(), (5, 5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
