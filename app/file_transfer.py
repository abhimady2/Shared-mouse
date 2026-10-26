import os
import socket
import threading
import time
import uuid
from pathlib import Path

from app.config import config
from app.connection import conn_manager
from app.protocol import encode_message, decode_stream, MSG_HELLO, MSG_HELLO_ACK, MSG_FILE_OFFER, MSG_FILE_ACK, MSG_FILE_DONE

CHUNK = 512 * 1024  # 512KB chunks

# Save received files here (created on first receive)
RECEIVE_DIR = Path.home() / "Downloads" / "ShareLink"


class FileTransferManager:
    """Large file transfer over a dedicated TCP connection so input streaming never blocks.

    Offer/ACK/progress travel on the main peer connection; raw file bytes go over a
    second connection to the same port, announced in the offer.
    """

    def __init__(self):
        self.transfers = {}  # id -> {"dir": "send"/"recv", "name": ..., "size": ..., "done": ..., "status": ...}
        self.lock = threading.Lock()
        self.on_update_cb = None

    def set_on_update(self, cb):
        self.on_update_cb = cb

    def _update(self, tid, **fields):
        with self.lock:
            t = self.transfers.get(tid)
            if t:
                t.update(fields)
                snapshot = dict(t)
        if self.on_update_cb:
            self.on_update_cb(snapshot)

    def send_file(self, filepath: str) -> bool:
        peer = conn_manager.get_peer_info()
        if not peer:
            return False
        path = Path(filepath)
        if not path.is_file():
            return False
        threading.Thread(target=self._send_worker, args=(path, peer), daemon=True).start()
        return True

    def _send_worker(self, path: Path, peer: dict):
        tid = uuid.uuid4().hex[:8]
        size = path.stat().st_size
        with self.lock:
            self.transfers[tid] = {"id": tid, "dir": "send", "name": path.name, "size": size,
                                   "done": 0, "status": "connecting"}
        try:
            # Dedicated data connection to the peer's tcp_port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(10.0)
            sock.connect((peer["ip"], int(peer["port"] if peer.get("port") == config.get("tcp_port") else peer.get("data_port") or config.get("tcp_port"))))

            # Handshake identifies this as a file-transfer connection
            sock.sendall(encode_message({
                "type": MSG_HELLO,
                "device_id": config.get("device_id"),
                "name": config.get("device_name"),
                "pin": config.get("pin"),
                "file_transfer": True,
                "file_id": tid,
                "filename": path.name,
                "size": size,
            }))

            sock.settimeout(15.0)
            raw = sock.recv(4096)
            msgs = decode_stream(bytearray(raw))
            if not msgs or msgs[0].get("type") != MSG_HELLO_ACK or msgs[0].get("status") != "OK":
                raise ConnectionError("Receiver rejected transfer")
            sock.settimeout(None)

            self._update(tid, status="sending")
            sent = 0
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    sock.sendall(chunk)
                    sent += len(chunk)
                    self._update(tid, done=sent)

            sock.settimeout(15.0)
            raw = sock.recv(4096)
            msgs = decode_stream(bytearray(raw))
            if msgs and msgs[0].get("type") == MSG_FILE_DONE:
                self._update(tid, status="complete")
                self._log(f"Sent '{path.name}' ({size / 1e6:.1f} MB) to {peer['name']}")
            else:
                raise ConnectionError("No completion confirmation from receiver")
        except Exception as e:
            self._update(tid, status=f"failed: {e}")
            self._log(f"Send failed for '{path.name}': {e}", "warning")
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def accept_transfer(self, client_sock: socket.socket, offer: dict, addr):
        """Called by connection.py when a peer opens a file-transfer connection."""
        tid = offer.get("file_id") or uuid.uuid4().hex[:8]
        filename = os.path.basename(offer.get("filename") or f"file_{tid}")
        size = int(offer.get("size", 0))

        RECEIVE_DIR.mkdir(parents=True, exist_ok=True)
        dest = RECEIVE_DIR / filename
        i = 1
        while dest.exists():
            dest = RECEIVE_DIR / f"{dest.stem}_{i}{dest.suffix}"
            i += 1

        with self.lock:
            self.transfers[tid] = {"id": tid, "dir": "recv", "name": dest.name, "size": size,
                                   "done": 0, "status": "receiving"}

        def _worker():
            try:
                client_sock.sendall(encode_message({"type": MSG_HELLO_ACK, "status": "OK"}))
                client_sock.settimeout(30.0)
                recv = 0
                with open(dest, "wb") as f:
                    while recv < size:
                        chunk = client_sock.recv(min(CHUNK, size - recv))
                        if not chunk:
                            break
                        f.write(chunk)
                        recv += len(chunk)
                        self._update(tid, done=recv)
                if recv == size:
                    client_sock.sendall(encode_message({"type": MSG_FILE_DONE}))
                    self._update(tid, status="complete")
                    self._log(f"Received '{dest.name}' ({size / 1e6:.1f} MB) -> {RECEIVE_DIR}")
                else:
                    dest.unlink(missing_ok=True)
                    raise ConnectionError(f"Incomplete: {recv}/{size} bytes")
            except Exception as e:
                self._update(tid, status=f"failed: {e}")
                self._log(f"Receive failed for '{dest.name}': {e}", "warning")
                try:
                    dest.unlink(missing_ok=True)
                except Exception:
                    pass
            finally:
                try:
                    client_sock.close()
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def get_transfers(self) -> list:
        with self.lock:
            return list(self.transfers.values())

    def _log(self, msg, level="info"):
        from app.state import state
        state.log(msg, level)


file_transfer = FileTransferManager()
