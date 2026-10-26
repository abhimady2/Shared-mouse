import socket
import json
import subprocess
import time
import threading
from app.config import config
from app.layout import layout

DISCOVERY_SERVICE_ID = "sharelink_v1"

# ARP table cache. Both discovery loops need it, but the table changes on the
# order of minutes - re-running arp.exe every couple of seconds just flashed a
# console window (the frozen exe is console-less, so Windows allocated a new
# one per spawn) and burned a process per beacon.
_ARP_TTL = 30.0
_arp_cache = {"t": 0.0, "ips": []}
_arp_lock = threading.Lock()

class PeerDiscovery:
    def __init__(self, on_peer_found=None):
        self.on_peer_found = on_peer_found
        self.discovered_peers = {} # device_id -> dict
        self.running = False
        self.broadcast_thread = None
        self.listener_thread = None
        self.prune_thread = None
        self.lock = threading.Lock()

    def start(self):
        if self.running:
            return
        self.running = True

        self.listener_thread = threading.Thread(target=self._listen_loop, daemon=True, name="DiscoveryListener")
        self.broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True, name="DiscoveryBroadcaster")
        self.prune_thread = threading.Thread(target=self._prune_loop, daemon=True, name="DiscoveryPruner")
        self.probe_thread = threading.Thread(target=self._active_probe_loop, daemon=True, name="DiscoveryProber")

        self.listener_thread.start()
        self.broadcast_thread.start()
        self.prune_thread.start()
        self.probe_thread.start()

    def stop(self):
        self.running = False

    def get_local_ip(self) -> str:
        """Determines the primary local LAN IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _arp_neighbors(self, prefix: str, local_ip: str) -> set[str]:
        """LAN IPs from the ARP table, refreshed at most once per _ARP_TTL."""
        now = time.time()
        with _arp_lock:
            if now - _arp_cache["t"] > _ARP_TTL:
                try:
                    out = subprocess.check_output(
                        ["arp", "-a"],
                        text=True,
                        errors="ignore",
                        timeout=2.0,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    _arp_cache["ips"] = [l.split()[0] for l in out.splitlines() if l.split()]
                    _arp_cache["t"] = now
                except Exception:
                    pass  # keep the last good table
            ips = list(_arp_cache["ips"])

        return {
            ip for ip in ips
            if ip.startswith(prefix) and not ip.endswith(".255") and ip != local_ip
        }

    def _get_discovery_targets(self) -> list[str]:
        targets = set(["255.255.255.255"])
        local_ip = self.get_local_ip()
        parts = local_ip.split(".")
        if len(parts) == 4 and parts[0] != "127":
            # Subnet broadcast (e.g. 192.168.1.255)
            targets.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
            # Discover active ARP neighbors across WiFi/Ethernet bridge
            targets |= self._arp_neighbors(f"{parts[0]}.{parts[1]}.{parts[2]}.", local_ip)
        return list(targets)

    def _broadcast_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        udp_port = int(config.get("udp_port", 42899))

        while self.running:
            try:
                beacon = {
                    "service": DISCOVERY_SERVICE_ID,
                    "device_id": config.get("device_id"),
                    "name": config.get("device_name"),
                    "tcp_port": int(config.get("tcp_port", 42898)),
                    "web_port": int(config.get("web_port", 42897)),
                    "width": layout.local_width,
                    "height": layout.local_height,
                    "pin": config.get("pin")
                }
                payload = json.dumps(beacon).encode('utf-8')
                targets = self._get_discovery_targets()
                for target_ip in targets:
                    try:
                        sock.sendto(payload, (target_ip, udp_port))
                    except Exception:
                        pass
            except Exception as e:
                pass
            time.sleep(2.0)
        sock.close()

    def _listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_port = int(config.get("udp_port", 42899))

        try:
            sock.bind(("0.0.0.0", udp_port))
        except Exception as e:
            print(f"[Discovery] Could not bind UDP discovery port {udp_port}: {e}")
            return

        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
                msg = json.loads(data.decode('utf-8'))

                if msg.get("service") != DISCOVERY_SERVICE_ID:
                    continue

                peer_ip = addr[0]
                my_ip = self.get_local_ip()

                # Ignore our own broadcasts
                if peer_ip in ("127.0.0.1", "localhost", my_ip):
                    continue

                peer_id = msg.get("device_id")
                # If peer copied our folder and has identical ID, give it an IP suffix
                if peer_id == config.get("device_id"):
                    peer_id = f"{peer_id}_{peer_ip.replace('.', '_')}"

                paired_devs = config.get("paired_devices", {})
                is_paired = (peer_id in paired_devs)

                peer_info = {
                    "device_id": peer_id,
                    "name": msg.get("name", f"PC ({peer_ip})"),
                    "ip": peer_ip,
                    "tcp_port": msg.get("tcp_port", 42898),
                    "web_port": msg.get("web_port", 42897),
                    "width": msg.get("width", 1920),
                    "height": msg.get("height", 1080),
                    "last_seen": time.time(),
                    "paired": is_paired
                }

                with self.lock:
                    is_new = peer_id not in self.discovered_peers
                    self.discovered_peers[peer_id] = peer_info

                if is_new and self.on_peer_found:
                    self.on_peer_found(peer_info)

            except Exception:
                pass

        sock.close()

    def _active_probe_loop(self):
        """Actively probes active ARP neighbors on tcp_port in case UDP broadcast is blocked across Wi-Fi/Ethernet."""
        tcp_port = int(config.get("tcp_port", 42898))
        while self.running:
            try:
                targets = self._get_discovery_targets()
                my_ip = self.get_local_ip()
                for target_ip in targets:
                    if target_ip in ("255.255.255.255", "127.0.0.1", my_ip) or target_ip.endswith(".255"):
                        continue
                    # Fast probe
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(0.5)
                        s.connect((target_ip, tcp_port))
                        s.close()

                        peer_id = f"peer_{target_ip.replace('.', '_')}"
                        peer_info = {
                            "device_id": peer_id,
                            "name": f"Windows PC ({target_ip})",
                            "ip": target_ip,
                            "tcp_port": tcp_port,
                            "web_port": int(config.get("web_port", 42897)),
                            "width": 1920,
                            "height": 1080,
                            "last_seen": time.time(),
                            "paired": True
                        }
                        with self.lock:
                            is_new = peer_id not in self.discovered_peers
                            self.discovered_peers[peer_id] = peer_info
                        if is_new and self.on_peer_found:
                            self.on_peer_found(peer_info)
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(3.0)

    def _prune_loop(self):
        while self.running:
            now = time.time()
            with self.lock:
                expired = [k for k, v in self.discovered_peers.items() if now - v["last_seen"] > 8.0]
                for k in expired:
                    del self.discovered_peers[k]
            time.sleep(4.0)

    def get_peers(self) -> list[dict]:
        with self.lock:
            return list(self.discovered_peers.values())

discovery = PeerDiscovery()
