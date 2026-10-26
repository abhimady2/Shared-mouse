# ShareLink DuoCast 🚀
### Cross-PC Mouse, Keyboard & Clipboard Mesh for Windows

**ShareLink DuoCast** is a high-performance, low-latency mouse, keyboard, and clipboard sharing software designed for seamless multi-PC setups on the same local network (Wi-Fi or Ethernet).

Just like Synergy or Mouse Without Borders, simply move your mouse past the border of your monitor, and your cursor and keyboard immediately transition to your neighboring PC with zero delay!

---

## ✨ Features

- **Zero-Config LAN Auto-Discovery**:
  - Automatically discovers PCs running on the same network using UDP broadcast beacons.
  - 1-Click "Connect" to pair instantly.
- **Bi-Directional Mesh Control**:
  - Both PCs run the exact same software.
  - Moving the physical mouse on PC 1 past the screen edge controls PC 2.
  - Moving the physical mouse on PC 2 past the opposing edge controls PC 1!
- **Low-Latency Input Streaming (`TCP_NODELAY`)**:
  - Sub-millisecond network delivery with Nagle's algorithm disabled.
  - Native Windows Win32 `SendInput` and low-level input hooks.
  - Supports mouse motion, clicks (Left, Right, Middle, X1, X2), and mouse wheel scroll.
  - Full keyboard event forwarding including modifier keys (`Ctrl`, `Shift`, `Alt`, `Win`).
- **Real-Time Clipboard Synchronization**:
  - Zero-CPU sequence number monitoring (`GetClipboardSequenceNumber`).
  - Automatically synchronizes text and copied snippets between computers.
  - Built-in echo loop prevention.
- **Fail-Safe Emergency Escape**:
  - Pressing <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>Esc</kbd> or <kbd>Scroll Lock</kbd> instantly returns cursor to the local screen.
  - Automatic 4.5s heartbeat watchdog brings cursor home if Wi-Fi disconnects.
- **Modern Glassmorphism Web Dashboard**:
  - Served locally on `http://localhost:42897`.
  - Interactive screen arrangement visualizer (Left, Right, Top, Bottom).
  - Live discovery radar, network ping telemetry, and transition tuning sliders.

---

## 🚀 Quick Start

### On PC 1 (Primary):
1. Simply double-click **`Run_ShareLink.bat`** (or run `python main.py`).
2. The web dashboard will automatically open at `http://localhost:42897`.

### On PC 2 (Secondary):
1. Copy the `Shared mouse` folder to PC 2.
2. Double-click **`Run_ShareLink.bat`**.
3. In the dashboard on either PC, you will see the other PC appear under **Zero-Config LAN Discovery**!
4. Click **Connect** (or use the 6-digit PIN).
5. Done! Move your mouse across the edge to control the second PC!

---

## 🖥️ Screen Arrangement

In the Web Dashboard, choose where the second PC is physically located relative to your monitor:
- **Right ▶**: Moving cursor to the right edge enters the left edge of PC 2.
- **◀ Left**: Moving cursor to the left edge enters the right edge of PC 2.
- **▲ Top**: Moving cursor to the top edge enters the bottom edge of PC 2.
- **▼ Bottom**: Moving cursor to the bottom edge enters the top edge of PC 2.

Resolution scaling automatically maps cursor coordinates between different monitor resolutions (e.g. 1080p laptop next to 4K monitor).

---

## ⌨️ Hotkeys & Shortcuts

| Action | Hotkey |
|---|---|
| **Emergency Breakout / Recenter Cursor** | <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>Esc</kbd> |
| **Alternative Breakout** | <kbd>Scroll Lock</kbd> |

---

## ⚙️ Configuration & Custom Ports

To run with custom ports from command line:
```bash
python main.py --port 42897 --tcp-port 42898 --udp-port 42899 --name "MyLaptop"
```

Settings are persistently saved to `config.json` and can be adjusted directly from the Web Dashboard.

---

## 🧪 Testing

Run the automated test suite anytime:
```bash
python tests/test_suite.py
```
