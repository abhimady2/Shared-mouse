import ctypes
from ctypes import wintypes
import hashlib
import time
import threading
import pyperclip
from app.config import config

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

class ClipboardSync:
    def __init__(self, on_change_callback=None):
        self.on_change_callback = on_change_callback
        self.last_seq = 0
        self.last_hash = ""
        self.ignored_hashes = set()
        self.lock = threading.Lock()
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.last_seq = user32.GetClipboardSequenceNumber()
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True, name="ClipboardMonitor")
        self.thread.start()

    def stop(self):
        self.running = False

    def _monitor_loop(self):
        while self.running:
            try:
                if not config.get("clipboard_sync", True):
                    time.sleep(0.5)
                    continue

                seq = user32.GetClipboardSequenceNumber()
                if seq != self.last_seq:
                    self.last_seq = seq
                    self._check_clipboard()
            except Exception as e:
                pass
            time.sleep(0.15)  # 150ms check interval, consumes <0.01% CPU

    def _check_clipboard(self):
        try:
            text = pyperclip.paste()
            if not text:
                return
            h = hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()

            with self.lock:
                if h in self.ignored_hashes:
                    # Content originated from remote peer, ignore to prevent echo
                    self.ignored_hashes.remove(h)
                    return
                if h == self.last_hash:
                    return
                self.last_hash = h

            if self.on_change_callback:
                self.on_change_callback("text", text, h)
        except Exception as e:
            # Clipboard might be momentarily locked by another app
            pass

    def set_remote_clipboard(self, kind: str, data: str, text_hash: str):
        """Sets clipboard from remote peer and marks its hash to prevent echoing."""
        if not config.get("clipboard_sync", True):
            return

        with self.lock:
            self.ignored_hashes.add(text_hash)
            # ponytail: capped so it can't grow unbounded - below we bump last_seq so
            # the monitor loop never fires for this hash, and so never removes it.
            if len(self.ignored_hashes) > 32:
                self.ignored_hashes = {text_hash}
            self.last_hash = text_hash

        try:
            if kind == "text":
                pyperclip.copy(data)
                # Update last sequence number so local loop doesn't fire immediately
                self.last_seq = user32.GetClipboardSequenceNumber()
        except Exception as e:
            print(f"[ClipboardSync] Error setting local clipboard: {e}")

clipboard = ClipboardSync()
