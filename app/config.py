import os
import sys
import json
import socket
import uuid
import secrets
from pathlib import Path

# ponytail: frozen builds are portable, so config.json lives next to the exe,
# not in PyInstaller's throwaway _MEIPASS dir.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent

CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "device_id": str(uuid.uuid4())[:8],
    "device_name": socket.gethostname(),
    "tcp_port": 42898,
    "udp_port": 42899,
    "web_port": 42897,
    "pin": f"{secrets.randbelow(900000) + 100000}",
    "auto_accept_lan": True,
    "clipboard_sync": True,
    "edge_threshold": 3,           # Pixels near edge to trigger switch
    "edge_delay_ms": 30,           # Delay in ms before switching (prevents accidental slips)
    "paired_devices": {},          # device_id -> { "name", "ip", "port", "trusted", "edge" }
    "emergency_hotkey": "Ctrl+Alt+Escape"
}

class ConfigManager:
    def __init__(self, filepath=CONFIG_FILE):
        self.filepath = Path(filepath)
        self.config = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        # Generate hardware-unique device ID based on local MAC address
        hw_suffix = hex(uuid.getnode())[-6:]
        self.config["device_id"] = f"{socket.gethostname()}_{hw_suffix}"
        self.config["device_name"] = socket.gethostname()

        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    # Don't overwrite device_id if it was copied from another machine
                    saved.pop("device_id", None)
                    saved.pop("device_name", None)
                    self.config.update(saved)
            except Exception as e:
                print(f"[Config] Error reading {self.filepath}: {e}, using defaults.")
        else:
            self.save()

        # Transient per-process identity override. Lets you run several copies on a
        # single machine to test the mesh (device_id normally derives from hostname+MAC,
        # so every copy on one box would otherwise look like the same device).
        # Deliberately not persisted.
        env_id = os.environ.get("SHARELINK_DEVICE_ID")
        if env_id:
            self.config["device_id"] = env_id
            self.config["device_name"] = os.environ.get("SHARELINK_DEVICE_NAME", env_id)

    def save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"[Config] Error saving config: {e}")

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save()

    def update(self, key_values: dict):
        self.config.update(key_values)
        self.save()

    def add_paired_device(self, dev_id, name, ip, port):
        devs = self.config.setdefault("paired_devices", {})
        # Preserve the assigned screen edge - reconnecting must not reshuffle the layout
        existing_edge = devs.get(dev_id, {}).get("edge")
        devs[dev_id] = {
            "name": name,
            "ip": ip,
            "port": port,
            "trusted": True,
            "edge": existing_edge,
        }
        self.save()

    def set_peer_edge(self, dev_id, edge):
        devs = self.config.setdefault("paired_devices", {})
        devs.setdefault(dev_id, {})["edge"] = edge
        self.save()

    def remove_paired_device(self, dev_id):
        if "paired_devices" in self.config and dev_id in self.config["paired_devices"]:
            del self.config["paired_devices"][dev_id]
            self.save()

config = ConfigManager()
