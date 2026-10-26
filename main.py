import os
import sys
import ctypes

if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import argparse
import asyncio
import webbrowser
import threading
import time
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from app.config import config
from app.state import state
from app.connection import conn_manager
from app import file_transfer as file_transfer_module
from app.file_transfer import file_transfer

__version__ = "1.0.3"

app = FastAPI(title="ShareLink DuoCast", version=__version__)

# Bundled web assets unpack to _MEIPASS under PyInstaller --onefile
WEB_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "web"
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

@app.get("/")
async def get_index():
    return FileResponse(str(WEB_DIR / "index.html"))

@app.get("/favicon.ico", include_in_schema=False)
async def get_favicon():
    return FileResponse(str(WEB_DIR / "favicon.ico"))

@app.get("/api/status")
async def get_status():
    payload = state.get_status_payload()
    payload["version"] = __version__
    payload["transfers"] = file_transfer.get_transfers()[-8:]
    payload["receive_dir"] = str(file_transfer_module.RECEIVE_DIR)
    return payload

@app.post("/api/send_file")
async def api_send_file(req: dict):
    path = req.get("path", "")
    ok = file_transfer.send_file(path)
    return {"status": "OK" if ok else "ERROR", "msg": "" if ok else "Not connected or file not found"}

@app.post("/api/upload_file")
async def api_upload_file(request: Request):
    """Browser fallback: receives the file body, spools to temp, then transfers it."""
    import tempfile
    from urllib.parse import unquote
    name = unquote(request.headers.get("x-filename", "upload.bin"))
    peer_id = request.headers.get("x-peer-id", "")
    tmp = Path(tempfile.gettempdir()) / f"sharelink_{int(time.time())}_{name}"
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        ok = file_transfer.send_file(str(tmp), peer_id)
        return {"status": "OK" if ok else "ERROR", "msg": "" if ok else "Not connected"}
    except Exception as e:
        return {"status": "ERROR", "msg": str(e)}
    finally:
        # ponytail: delete spool after send starts; sender streams from it in a thread
        threading.Timer(120, lambda: tmp.unlink(missing_ok=True)).start()

@app.post("/api/pick_and_send")
async def api_pick_and_send():
    """Native file picker (pywebview) -> transfer, so browsers never touch the file."""
    try:
        import webview
        files = webview.windows[0].create_file_dialog(
            webview.FOLDER_DIALOG if False else webview.OPEN_DIALOG,
            allow_multiple=False,
        )
        if files:
            ok = file_transfer.send_file(files[0])
            return {"status": "OK" if ok else "ERROR", "msg": files[0] if ok else "Not connected or file not found"}
        return {"status": "CANCELLED"}
    except Exception as e:
        return {"status": "ERROR", "msg": str(e)}

@app.get("/api/emergency")
@app.post("/api/emergency")
async def api_emergency():
    state.emergency_breakout()
    return {"status": "OK", "msg": "Emergency reset completed: local control restored."}

@app.get("/api/reset")
@app.post("/api/reset")
async def api_reset():
    state.emergency_breakout()
    return {"status": "OK", "msg": "Emergency reset completed: local control restored."}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    state.register_ws(websocket)
    try:
        # Send initial full state immediately
        await websocket.send_json({
            "type": "status",
            "data": state.get_status_payload()
        })

        while True:
            data = await websocket.receive_json()
            action = data.get("action")

            if action == "connect":
                ip = data.get("ip")
                port = int(data.get("port", 42898))
                pin = data.get("pin", "")
                success, msg = conn_manager.connect_to_peer(ip, port, pin)
                state.log(f"Connection attempt to {ip}:{port}: {msg}", "success" if success else "warning")
                state.broadcast_status()

            elif action == "disconnect":
                conn_manager.disconnect()
                state.broadcast_status()

            elif action == "disconnect_peer":
                # Drops one PC; the rest of the mesh stays up. Without a peer_id
                # this degrades to "disconnect everything".
                pid = data.get("peer_id", "")
                conn_manager.disconnect(pid or None)
                state.broadcast_status()

            elif action == "emergency":
                state.emergency_breakout()

            elif action == "focus_peer":
                # Drives a specific peer directly - needed for peers past the
                # 4-edge ceiling, which the cursor cannot reach.
                pid = data.get("peer_id", "")
                if state.focus_peer(pid):
                    state.log(f"Switched control to {pid}", "info")
                else:
                    state.log(f"Could not switch to {pid}", "warning")
                state.broadcast_status()

            elif action == "set_peer_edge":
                # Manual arrangement: overrides the auto-assigned side.
                pid = data.get("peer_id", "")
                edge = data.get("edge", "")
                if conn_manager.set_edge(pid, edge):
                    state.log(f"Moved {pid} to the {edge} edge", "info")
                else:
                    state.log(f"Could not move {pid} to {edge}", "warning")
                state.broadcast_status()

            elif action == "update_config":
                data.pop("action", None)
                config.update(data)
                state.broadcast_status()

    except WebSocketDisconnect:
        state.unregister_ws(websocket)
    except Exception as e:
        state.unregister_ws(websocket)


def open_browser_delayed(url: str, delay: float = 1.2):
    def _open():
        time.sleep(delay)
        webbrowser.open(url)
    t = threading.Thread(target=_open, daemon=True)
    t.start()


class DashboardApi:
    """Bridge exposed to dashboard JS as window.pywebview.api."""

    window = None
    icon = None

    def minimize_to_tray(self):
        if self.window:
            self.window.hide()
        if self.icon:
            self.icon.visible = True


def run_webview(url: str, web_port: int):
    """Runs the dashboard in a native lightweight window (Edge WebView2 on Windows)."""
    import webview
    api = DashboardApi()
    window = webview.create_window(
        "ShareLink DuoCast",
        url,
        width=1180,
        height=800,
        min_size=(900, 620),
        js_api=api,
    )
    api.window = window
    _attach_tray(window, api)
    webview.start()


def _attach_tray(window, api):
    """Minimize-to-tray: minimizing the native window (or the dashboard button)
    hides it; the tray icon restores it or exits the app (engine keeps sharing
    input while hidden)."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return  # ponytail: tray is a nicety, not core; skip if deps missing

    img = None
    icon_file = WEB_DIR / "tray.png"
    if not icon_file.exists():
        icon_file = WEB_DIR / "favicon.png"
    if not icon_file.exists():
        icon_file = WEB_DIR / "logo.png"
    if icon_file.exists():
        try:
            with Image.open(icon_file) as src_img:
                img = src_img.resize((64, 64), Image.Resampling.LANCZOS).copy()
        except Exception:
            img = None

    if img is None:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((6, 6, 58, 58), fill=(37, 99, 235))

    icon = pystray.Icon(
        "sharelink", img, "ShareLink DuoCast",
        pystray.Menu(
            pystray.MenuItem("Open ShareLink", lambda *_: window.show(), default=True),
            pystray.MenuItem("Exit", lambda *_: (icon.stop(), window.destroy())),
        ),
    )

    def on_minimized():
        window.hide()
        icon.visible = True

    window.events.minimized += on_minimized
    api.icon = icon
    # pystray runs its own message loop; a daemon thread keeps the main thread
    # free for WebView2, which must own it on Windows.
    threading.Thread(target=icon.run, daemon=True, name="TrayIcon").start()


def main():
    parser = argparse.ArgumentParser(description="ShareLink DuoCast — Multi-PC Mouse, Keyboard & Clipboard Sharing")
    parser.add_argument("--port", type=int, default=None, help="Web Dashboard Port (default: 42897)")
    parser.add_argument("--tcp-port", type=int, default=None, help="TCP Input Stream Port (default: 42898)")
    parser.add_argument("--udp-port", type=int, default=None, help="UDP Discovery Port (default: 42899)")
    parser.add_argument("--name", type=str, default=None, help="Custom Device Name")
    parser.add_argument("--no-browser", action="store_true", help="Do not open any dashboard window")
    parser.add_argument("--browser", action="store_true", help="Open dashboard in browser tab instead of native window")
    args = parser.parse_args()

    if args.port:
        config.set("web_port", args.port)
    if args.tcp_port:
        config.set("tcp_port", args.tcp_port)
    if args.udp_port:
        config.set("udp_port", args.udp_port)
    if args.name:
        config.set("device_name", args.name)

    web_port = int(config.get("web_port", 42897))
    url = f"http://localhost:{web_port}"

    # Windowed (console=False) builds have no standard streams at all - sys.stdout
    # and sys.stderr are literally None. uvicorn's log formatter calls
    # sys.stderr.isatty() while configuring logging and raises on that, killing
    # startup before the server ever binds. Give them a real sink first.
    if sys.stdout is None or sys.stderr is None:
        _sink = open(os.devnull, "w", encoding="utf-8", errors="replace")
        if sys.stdout is None:
            sys.stdout = _sink
        if sys.stderr is None:
            sys.stderr = _sink

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 60)
    print("  [ShareLink DuoCast] Starting Mesh Engine")
    print(f"  * Device Name: {config.get('device_name')}")
    print(f"  * Pairing PIN: {config.get('pin')}")
    print(f"  * Web Dashboard: {url}")
    print(f"  * Input Port: {config.get('tcp_port')} (TCP_NODELAY)")
    print(f"  * Discovery Port: {config.get('udp_port')} (UDP Broadcast)")
    print(f"  * Emergency Escape: Ctrl + Alt + Escape")
    # Gracefully handle already running instance
    import socket
    test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test_sock.settimeout(0.4)
    try:
        test_sock.connect(("127.0.0.1", web_port))
        test_sock.close()
        print("=" * 60)
        print("  [ShareLink DuoCast] Already running in background!")
        print(f"  * Opening Web Dashboard: {url}")
        print("=" * 60)
        if not args.no_browser:
            webbrowser.open(url)
        return
    except Exception:
        pass

    # Attach event loop to state manager
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    state.set_event_loop(loop)
    state.start_all()

    # Native window (default) or browser tab; falls back to browser if pywebview missing
    use_webview = not (args.browser or args.no_browser)
    if use_webview:
        try:
            import webview  # noqa: F401
        except ImportError:
            use_webview = False
    # Run Uvicorn server
    uvicorn_config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=web_port,
        log_level="warning",
        loop="asyncio"
    )
    server = uvicorn.Server(uvicorn_config)

    if use_webview:
        # pywebview must own the main thread; uvicorn serves from a daemon thread
        threading.Thread(target=lambda: loop.run_until_complete(server.serve()), daemon=True).start()
        time.sleep(1.0)
        try:
            run_webview(url, web_port)
        except Exception as e:
            # No Edge WebView2 runtime on this PC (pywebview imports its .NET
            # interop at module load and raises). Keep serving in the browser
            # instead of exiting with no window at all.
            print(f"[ShareLink] Native window unavailable ({e}); using browser dashboard.")
            if not args.no_browser:
                webbrowser.open(url)
            # uvicorn is on a daemon thread - without this wait the process
            # would return from main() and the dashboard would vanish instantly.
            try:
                while not server.should_exit:
                    time.sleep(0.5)
            except (KeyboardInterrupt, SystemExit):
                pass
        server.should_exit = True
        time.sleep(0.5)
        state.stop_all()
    else:
        if not args.no_browser:
            open_browser_delayed(url)
        try:
            loop.run_until_complete(server.serve())
        except (KeyboardInterrupt, SystemExit):
            print("\nShutting down ShareLink DuoCast...")
        finally:
            state.stop_all()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # The shipped exe is windowed (console=False), so PyInstaller's crash
        # dialog is the only place a traceback would ever surface - and it can't
        # be copied out of. Leave one on disk next to config.json instead.
        import traceback
        from app.config import BASE_DIR
        try:
            with open(BASE_DIR / "sharelink_crash.log", "a", encoding="utf-8") as f:
                f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                traceback.print_exc(file=f)
        except Exception:
            pass
        raise
