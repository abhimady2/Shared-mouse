import ctypes
import win32api
import win32con
import time
from app.config import config

# Enable Per-Monitor DPI awareness so coordinates match physical pixels (1920x1080 instead of virtualized 1536x864)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

class ScreenLayout:
    def __init__(self):
        self.refresh_screen_size()
        self.last_edge_hit_time = 0
        self.last_edge_hit_side = None
        self.cooldown_until = 0.0

    def refresh_screen_size(self):
        self.local_width = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        self.local_height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        return self.local_width, self.local_height

    def get_screen_info(self) -> dict:
        w, h = self.refresh_screen_size()
        return {"width": w, "height": h}

    def set_cooldown(self, seconds: float = 1.0):
        self.cooldown_until = time.time() + seconds

    def check_edge_crossing(self, x: int, y: int, active_edges=None) -> tuple[bool, str, float]:
        """
        Checks if the cursor (x, y) has hit an edge that has a peer assigned to it.
        active_edges: set of edges currently owned by a connected peer ('right','left','top','bottom').
        Returns: (did_cross, edge_name, entry_percentage)
        """
        # Guard: cooldown prevents rapid re-triggering / bouncing
        if time.time() < self.cooldown_until:
            return False, "", 0.0

        # Guard: Ignore uninitialized, virtualized, or origin (0, 0) coordinates
        if (x <= 0 and y <= 0) or x < 0 or y < 0:
            return False, "", 0.0

        # A bare string would iterate as characters and silently match nothing
        if isinstance(active_edges, str):
            active_edges = {active_edges}
        edges = set(active_edges or ())
        if not edges:
            return False, "", 0.0

        threshold = int(config.get("edge_threshold", 3))
        delay_ms = int(config.get("edge_delay_ms", 30))

        hit = False
        edge_name = ""
        pct = 0.5

        # Also exclude extreme 10px corners from triggering to avoid accidental triggers from OS hot corners
        w, h = self.local_width, self.local_height
        in_y_range = (10 <= y <= h - 10)
        in_x_range = (10 <= x <= w - 10)

        if "right" in edges and x >= (w - threshold) and in_y_range:
            hit = True
            edge_name = "right"
            pct = max(0.05, min(0.95, y / max(1, h)))
        elif "left" in edges and x <= threshold and in_y_range:
            hit = True
            edge_name = "left"
            pct = max(0.05, min(0.95, y / max(1, h)))
        elif "top" in edges and y <= threshold and in_x_range:
            hit = True
            edge_name = "top"
            pct = max(0.05, min(0.95, x / max(1, w)))
        elif "bottom" in edges and y >= (h - threshold) and in_x_range:
            hit = True
            edge_name = "bottom"
            pct = max(0.05, min(0.95, x / max(1, w)))

        if hit:
            now = time.time() * 1000
            if self.last_edge_hit_side == edge_name:
                if (now - self.last_edge_hit_time) >= delay_ms:
                    # Successfully crossed with hysteresis
                    self.last_edge_hit_side = None
                    self.set_cooldown(0.8)
                    return True, edge_name, pct
            else:
                self.last_edge_hit_side = edge_name
                self.last_edge_hit_time = now
        else:
            self.last_edge_hit_side = None

        return False, "", 0.0

    def calculate_remote_entry(self, edge: str, pct: float, remote_w: int, remote_h: int) -> tuple[int, int]:
        """
        Calculates safe entry (x, y) coordinates on the remote machine when crossing from host.
        Uses 40px margin and clamps away from extreme corners so cursor never touches return threshold upon entry.
        """
        margin = 40
        safe_y = int(max(0.1, min(0.9, pct)) * remote_h)
        safe_x = int(max(0.1, min(0.9, pct)) * remote_w)

        if edge == "right": # Host pushed right -> Remote enters from left
            return margin, safe_y
        elif edge == "left": # Host pushed left -> Remote enters from right
            return remote_w - margin, safe_y
        elif edge == "top": # Host pushed top -> Remote enters from bottom
            return safe_x, remote_h - margin
        elif edge == "bottom": # Host pushed bottom -> Remote enters from top
            return safe_x, margin
        return remote_w // 2, remote_h // 2

    def check_return_edge(self, x: int, y: int, host_edge: str, remote_w: int, remote_h: int) -> tuple[bool, float]:
        """
        Checks if the remote cursor has hit the return edge towards host.
        Requires cursor to be firmly against the edge with hysteresis to avoid accidental returns.
        """
        threshold = 3
        hit = False
        pct = 0.5

        if host_edge == "right" and x <= threshold:
            hit = True
            pct = max(0.05, min(0.95, y / max(1, remote_h)))
        elif host_edge == "left" and x >= (remote_w - 1 - threshold):
            hit = True
            pct = max(0.05, min(0.95, y / max(1, remote_h)))
        elif host_edge == "top" and y >= (remote_h - 1 - threshold):
            hit = True
            pct = max(0.05, min(0.95, x / max(1, remote_w)))
        elif host_edge == "bottom" and y <= threshold:
            hit = True
            pct = max(0.05, min(0.95, x / max(1, remote_w)))

        if hit:
            now = time.time() * 1000
            if getattr(self, "_return_hit_start", 0) > 0:
                if (now - self._return_hit_start) >= 60: # 60ms intentional hold against border
                    self._return_hit_start = 0
                    return True, pct
            else:
                self._return_hit_start = now
        else:
            self._return_hit_start = 0

        return False, 0.0

    def calculate_host_return(self, host_edge: str, pct: float) -> tuple[int, int]:
        """
        Calculates local cursor coordinates when returning to host.
        Places cursor 40px away from the edge so it never immediately re-crosses.
        """
        margin = 40
        w, h = self.local_width, self.local_height
        safe_y = int(max(0.1, min(0.9, pct)) * h)
        safe_x = int(max(0.1, min(0.9, pct)) * w)

        if host_edge == "right":
            return w - margin, safe_y
        elif host_edge == "left":
            return margin, safe_y
        elif host_edge == "top":
            return safe_x, margin
        elif host_edge == "bottom":
            return safe_x, h - margin
        return w // 2, h // 2

layout = ScreenLayout()
