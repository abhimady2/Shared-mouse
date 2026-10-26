import queue
import time
import threading
import ctypes
from ctypes import wintypes
import win32api
import pynput
from pynput.keyboard import Key, KeyCode

from app.config import config
from app.layout import layout
from app.input_injector import injector
from app.connection import conn_manager
from app.protocol import (
    MSG_MOUSE_MOVE, MSG_MOUSE_BUTTON, MSG_MOUSE_WHEEL,
    MSG_KEY_EVENT, MSG_SWITCH, MSG_RETURN
)

user32 = ctypes.windll.user32

MODE_LOCAL = "LOCAL"
MODE_REMOTE_OUT = "REMOTE_OUT" # Local PC is sending input to remote PC
MODE_REMOTE_IN = "REMOTE_IN"   # Local PC is receiving input from host PC

class InputCaptureManager:
    def __init__(self, send_callback=None, on_state_change=None, is_connected_cb=None):
        self.send_callback = send_callback
        self.on_state_change = on_state_change
        self.is_connected_cb = is_connected_cb
        self.mode = MODE_LOCAL

        self.mouse_listener = None
        self.keyboard_listener = None
        self.tracking_thread = None
        self.sender_thread = None
        self.running = False
        self.lock = threading.RLock()

        # Outbound packet queue: hooks must never block on socket I/O. A stalled
        # sendall inside a low-level hook callback makes Windows delay/drop every
        # following event - that was the multi-second cursor freeze.
        self._send_q = queue.Queue()
        self._move_acc_lock = threading.Lock()
        self._pend_dx = 0
        self._pend_dy = 0

        # Pressed modifiers for emergency detection
        self.pressed_keys = set()
        self.anchor_x = 0
        self.anchor_y = 0
        self.is_repositioning = False
        # Which peer currently owns our input (REMOTE_OUT = we drive it,
        # REMOTE_IN = it drives us). Empty in MODE_LOCAL.
        self.active_peer_id = ""
        self.active_remote_edge = ""
        self._last_esc_time = 0.0

        # Tracking telemetry & safety
        self.last_mouse_activity = time.time()
        self.last_remote_activity = time.time()
        self.packets_sent = 0
        self.packets_recv = 0

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self.tracking_thread = threading.Thread(
                target=self._edge_tracker_loop,
                daemon=True,
                name="EdgeTracker"
            )
            self.tracking_thread.start()
            self.sender_thread = threading.Thread(
                target=self._send_loop,
                daemon=True,
                name="InputSender"
            )
            self.sender_thread.start()

    def stop(self):
        with self.lock:
            self.running = False
            self._stop_hooks()

    def _set_mode(self, new_mode: str):
        self.mode = new_mode
        self.last_remote_activity = time.time()
        if self.on_state_change:
            self.on_state_change(self.mode)

    def emergency_reset(self):
        """Unconditionally resets input capture, detaches all hooks, and centers cursor."""
        with self.lock:
            # Notify peer to also drop out of remote mode before releasing hooks
            # (prevents the peer being stranded in REMOTE_IN with no return path)
            self._drop_queued()
            if self.send_callback and self.mode != MODE_LOCAL and self.active_peer_id:
                self._enqueue(
                    {"type": MSG_RETURN, "edge": self.active_remote_edge, "entry_pct": 0.5},
                    self.active_peer_id
                )
            self._stop_hooks()
            self._set_mode(MODE_LOCAL)
            self.active_remote_edge = ""
            self.active_peer_id = ""
            layout.set_cooldown(1.5)
            w, h = layout.local_width, layout.local_height
            injector.set_cursor_pos(w // 2, h // 2)
            injector.release_all_modifiers()
            print("[InputCapture] Emergency reset: hooks stopped, mode=LOCAL, cursor centered.")

    def on_peer_disconnected(self, peer_id: str):
        """Called by the connection manager when one specific peer drops.

        Only gives up remote mode if that peer was the one we were driving (or
        being driven by) - an unrelated peer disconnecting must not interrupt a
        live session on another screen.
        """
        with self.lock:
            if peer_id == self.active_peer_id and self.mode != MODE_LOCAL:
                self.emergency_reset()

    def _edge_tracker_loop(self):
        """Monitors cursor and transitions between local and remote states."""
        while self.running:
            try:
                # SAFETY: if the peer we are currently driving has gone away, drop
                # back to local. Checked against the *specific* peer, not "any peer".
                if self.mode != MODE_LOCAL:
                    if not self.active_peer_id or (
                        self.is_connected_cb and not self.is_connected_cb(self.active_peer_id)
                    ):
                        self.emergency_reset()

                if self.mode == MODE_LOCAL:
                    cx, cy = injector.get_cursor_pos()
                    # Only edges that actually have a peer behind them can trigger
                    crossed, edge, pct = layout.check_edge_crossing(cx, cy, conn_manager.assigned_edges())
                    if crossed:
                        peer_id = conn_manager.peer_for_edge(edge)
                        if peer_id:
                            self.switch_to_remote(edge, pct, peer_id)

                elif self.mode == MODE_REMOTE_OUT:
                    pass  # ponytail: liveness handled by TCP heartbeat; idle bail-out caused mode flapping

                elif self.mode == MODE_REMOTE_IN:
                    # In REMOTE_IN mode, monitor if cursor hits return edge
                    # (liveness handled by TCP heartbeat; idle bail-out returned control mid-pause)
                    cx, cy = injector.get_cursor_pos()
                    w, h = layout.local_width, layout.local_height
                    hit, pct = layout.check_return_edge(cx, cy, self.active_remote_edge, w, h)
                    if hit:
                        self.return_to_host(pct)
            except Exception:
                pass
            time.sleep(0.012) # ~80Hz polling rate

    def switch_to_remote(self, edge: str, pct: float, peer_id: str):
        """Switches local input capture to forward all inputs to the given remote PC."""
        with self.lock:
            if self.mode != MODE_LOCAL:
                return
            # Safety: Must be actively connected to that peer
            if self.is_connected_cb and not self.is_connected_cb(peer_id):
                return

            self.active_peer_id = peer_id
            self.active_remote_edge = edge
            self._set_mode(MODE_REMOTE_OUT)
            self.last_remote_activity = time.time()
            layout.set_cooldown(1.0)

            # Anchor at screen center: edge-anchored cursors clamp to ~2px deltas on Windows
            w, h = layout.local_width, layout.local_height
            self.anchor_x = w // 2
            self.anchor_y = h // 2

            # Notify remote peer of switch
            if self.send_callback:
                self.send_callback({
                    "type": MSG_SWITCH,
                    "edge": edge,
                    "entry_pct": pct
                }, peer_id)

            # Position anchor cleanly
            self.is_repositioning = True
            injector.set_cursor_pos(self.anchor_x, self.anchor_y)
            time.sleep(0.01)
            self.is_repositioning = False

            # Start low-level input hooks with suppression
            self._start_hooks()

    def return_to_local(self, pct: float = 0.5):
        """Restores input control back to local PC."""
        with self.lock:
            if self.mode == MODE_LOCAL:
                return
            self._stop_hooks()
            self._drop_queued()
            self._set_mode(MODE_LOCAL)
            layout.set_cooldown(1.2)

            # Calculate and position local cursor
            rx, ry = layout.calculate_host_return(self.active_remote_edge or "right", pct)
            injector.set_cursor_pos(rx, ry)
            injector.release_all_modifiers()
            self.active_peer_id = ""

    def return_to_host(self, pct: float):
        """Called when this PC was remote target and cursor returns back to host."""
        with self.lock:
            if self.mode != MODE_REMOTE_IN:
                return
            self._stop_hooks()
            self._set_mode(MODE_LOCAL)
            layout.set_cooldown(1.2)
            if self.send_callback and self.active_peer_id:
                self._enqueue({
                    "type": MSG_RETURN,
                    "edge": self.active_remote_edge,
                    "entry_pct": pct
                }, self.active_peer_id)
            injector.release_all_modifiers()

            # Nudge cursor off the return edge: a cursor parked on this machine's own
            # crossing edge re-triggers switch_to_remote once cooldown expires,
            # which teleports the host's cursor back to its entry point mid-move.
            w, h = layout.local_width, layout.local_height
            cx, cy = injector.get_cursor_pos()
            edge = self.active_remote_edge or "right"
            if edge == "left":      # returned via our right edge
                injector.set_cursor_pos(min(cx, w - 40), cy)
            elif edge == "right":   # returned via our left edge
                injector.set_cursor_pos(max(40, cx), cy)
            elif edge == "top":     # returned via our bottom edge
                injector.set_cursor_pos(cx, min(cy, h - 40))
            elif edge == "bottom":  # returned via our top edge
                injector.set_cursor_pos(cx, max(40, cy))
            self.active_peer_id = ""

    def handle_remote_switch(self, edge: str, entry_pct: float, peer_id: str = ""):
        """Called when this PC receives MSG_SWITCH from the host driving it."""
        with self.lock:
            # Stop any local hooks before becoming remote receiver
            self._stop_hooks()
            self.active_remote_edge = edge
            self.active_peer_id = peer_id
            self._set_mode(MODE_REMOTE_IN)
            self.last_remote_activity = time.time()
            layout.set_cooldown(1.0)
            entry_x, entry_y = layout.calculate_remote_entry(edge, entry_pct, layout.local_width, layout.local_height)
            injector.set_cursor_pos(entry_x, entry_y)

    def handle_remote_return(self, entry_pct: float, peer_id: str = ""):
        """Called when host receives MSG_RETURN from a remote peer."""
        with self.lock:
            # Ignore a stale return from a peer we are not currently driving
            if self.mode != MODE_REMOTE_OUT:
                return
            if peer_id and self.active_peer_id and peer_id != self.active_peer_id:
                return
        self.return_to_local(entry_pct)

    # --- Outbound queue: hooks accumulate packets, one thread sends them ---

    def _take_pending_move(self):
        with self._move_acc_lock:
            dx, dy = self._pend_dx, self._pend_dy
            self._pend_dx = 0
            self._pend_dy = 0
        return dx, dy

    def _enqueue(self, msg: dict, peer_id: str):
        """Queues a packet for the sender thread. Any accumulated mouse motion
        is queued first so move-before-click ordering is preserved."""
        dx, dy = self._take_pending_move()
        if dx or dy:
            self._send_q.put(({"type": MSG_MOUSE_MOVE, "dx": dx, "dy": dy}, peer_id))
        self._send_q.put((msg, peer_id))

    def _drop_queued(self):
        while True:
            try:
                self._send_q.get_nowait()
            except queue.Empty:
                break
        self._take_pending_move()

    def _send_loop(self):
        while self.running:
            try:
                msg, peer_id = self._send_q.get(timeout=0.005)
            except queue.Empty:
                # No discrete events pending - flush coalesced mouse motion.
                # This caps move traffic at ~200 pkt/s instead of one packet
                # per hook callback (up to ~1000/s on gaming mice).
                dx, dy = self._take_pending_move()
                if (dx or dy) and self.send_callback and self.active_peer_id:
                    self.packets_sent += 1
                    self.send_callback(
                        {"type": MSG_MOUSE_MOVE, "dx": dx, "dy": dy},
                        self.active_peer_id
                    )
                continue
            if self.send_callback:
                self.packets_sent += 1
                self.send_callback(msg, peer_id)

    def _start_hooks(self):
        try:
            self.mouse_listener = pynput.mouse.Listener(
                on_move=self._on_mouse_move,
                on_click=self._on_mouse_click,
                on_scroll=self._on_mouse_scroll,
                suppress=True
            )
            self.mouse_listener.start()

            self.keyboard_listener = pynput.keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release,
                suppress=True
            )
            self.keyboard_listener.start()
        except Exception as e:
            print(f"[InputCapture] Error starting hooks: {e}")

    def _stop_hooks(self):
        if self.mouse_listener:
            try:
                self.mouse_listener.stop()
            except Exception:
                pass
            self.mouse_listener = None

        if self.keyboard_listener:
            try:
                self.keyboard_listener.stop()
            except Exception:
                pass
            self.keyboard_listener = None

        self.pressed_keys.clear()

    # --- Mouse Hook Callbacks ---

    def _on_mouse_move(self, x, y):
        if self.mode != MODE_REMOTE_OUT or self.is_repositioning:
            return

        dx = x - self.anchor_x
        dy = y - self.anchor_y

        if dx == 0 and dy == 0:
            return

        # ponytail: no pull-back gesture — return is via the remote's return edge
        # (35px cumulative reverse-motion trigger caused constant mode flapping)
        self.last_remote_activity = time.time()

        with self._move_acc_lock:
            self._pend_dx += dx
            self._pend_dy += dy

        # Re-center only once drift gets large. Re-centering on every event floods
        # the hook with synthetic moves (each SetCursorPos generates one) and the
        # late ones slip past is_repositioning as 1-2px ghost deltas = jitter.
        if abs(dx) > 150 or abs(dy) > 150:
            self.is_repositioning = True
            injector.set_cursor_pos(self.anchor_x, self.anchor_y)
            self.is_repositioning = False

    def _on_mouse_click(self, x, y, button, pressed):
        if self.mode != MODE_REMOTE_OUT:
            return

        btn_map = {
            pynput.mouse.Button.left: "left",
            pynput.mouse.Button.right: "right",
            pynput.mouse.Button.middle: "middle",
            pynput.mouse.Button.x1: "x1",
            pynput.mouse.Button.x2: "x2",
        }
        btn_name = btn_map.get(button, "left")
        action = "down" if pressed else "up"

        self._enqueue({
            "type": MSG_MOUSE_BUTTON,
            "btn": btn_name,
            "action": action
        }, self.active_peer_id)

    def _on_mouse_scroll(self, x, y, dx, dy):
        if self.mode != MODE_REMOTE_OUT:
            return

        if dy != 0:
            self._enqueue({
                "type": MSG_MOUSE_WHEEL,
                "delta": int(dy * 120),
                "horizontal": False
            }, self.active_peer_id)
        elif dx != 0:
            self._enqueue({
                "type": MSG_MOUSE_WHEEL,
                "delta": int(dx * 120),
                "horizontal": True
            }, self.active_peer_id)

    # --- Keyboard Hook Callbacks ---

    def _extract_vk(self, key):
        vk = None
        if hasattr(key, 'vk') and key.vk is not None:
            vk = key.vk
        elif hasattr(key, 'value') and hasattr(key.value, 'vk'):
            vk = key.value.vk
        elif hasattr(key, 'char') and key.char:
            res = win32api.VkKeyScan(key.char)
            if res != -1:
                vk = res & 0xFF
        return vk

    def _on_key_press(self, key):
        if self.mode != MODE_REMOTE_OUT:
            return

        vk = self._extract_vk(key)
        if vk is not None:
            self.pressed_keys.add(vk)

        # Check for Emergency Escape:
        # 1. Ctrl + Alt + Escape (VK_ESCAPE = 0x1B)
        # 2. ScrollLock (VK_SCROLL = 0x91)
        # 3. Double-tap Escape within 400ms
        is_ctrl = (0x11 in self.pressed_keys or 0xA2 in self.pressed_keys or 0xA3 in self.pressed_keys)
        is_alt = (0x12 in self.pressed_keys or 0xA4 in self.pressed_keys or 0xA5 in self.pressed_keys)
        is_esc = (vk == 0x1B)
        is_scroll = (vk == 0x91)

        now = time.time()
        double_esc = False
        if is_esc:
            if getattr(self, "_last_esc_time", 0) > 0 and (now - self._last_esc_time) < 0.4:
                double_esc = True
            self._last_esc_time = now

        if (is_ctrl and is_alt and is_esc) or is_scroll or double_esc:
            print("[InputCapture] Emergency Escape Hotkey Triggered!")
            self.emergency_reset()
            return

        if vk is not None:
            self._enqueue({
                "type": MSG_KEY_EVENT,
                "vk": vk,
                "action": "down"
            }, self.active_peer_id)

    def _on_key_release(self, key):
        if self.mode != MODE_REMOTE_OUT:
            return

        vk = self._extract_vk(key)
        if vk is not None and vk in self.pressed_keys:
            self.pressed_keys.discard(vk)

        if vk is not None:
            self._enqueue({
                "type": MSG_KEY_EVENT,
                "vk": vk,
                "action": "up"
            }, self.active_peer_id)

input_capture = InputCaptureManager()
