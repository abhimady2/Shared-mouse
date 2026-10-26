# AGENTS.md

Welcome to **ShareLink DuoCast** (Shared mouse)! This file provides the high-level project map and architectural rules for this repository.

## 🦅 Project Overview
ShareLink DuoCast is a high-performance, low-latency cross-PC mouse, keyboard, and clipboard mesh software for Windows. It provides zero-config LAN auto-discovery, bi-directional mesh control across screen edges, sub-millisecond input streaming (`TCP_NODELAY`), real-time clipboard synchronization, native file drag-and-drop transfers, and a glassmorphism web dashboard served natively via WebView2 or modern browsers.

## 🗺️ Project Structure
- `main.py`: Entrypoint orchestrating the FastAPI server, WebSocket hub, native WebView2 window, and system tray integration.
- `app/`:
  - `config.py`: Persistent configuration manager (`config.json`).
  - `state.py`: Global application state, telemetry tracking, and event dispatch.
  - `connection.py`: TCP network client & server handling low-latency bi-directional input streaming.
  - `discovery.py`: UDP broadcast discovery beaconing on LAN.
  - `input_capture.py`: Low-level Win32 mouse and keyboard hooks with worker queue and move coalescing.
  - `input_injector.py`: Win32 `SendInput` synthesizers.
  - `layout.py`: Multi-monitor virtual edge arrangement and cross-PC coordinate translation.
  - `clipboard_sync.py`: Win32 clipboard sequence monitoring and deduplicated text synchronization.
  - `file_transfer.py`: Streaming chunks for cross-PC file exchange.
  - `protocol.py`: Length-prefixed framing and JSON payload serializer/deserializer.
- `web/`: Frontend dashboard assets (`index.html`, `style.css`, `app.js`).
- `tests/`: Automated unit and integration test suite (`test_suite.py`).
- `ShareLink.spec` & `Build_Exe.bat`: PyInstaller configuration and portable executable build script.

## ⚖️ Architectural Rules
1. **DOX Protocol**: Always check for `AGENTS.md` in the current and parent directories before editing. Update them when logic changes.
2. **Zero-Defect Mandate**: Never write placeholders or partial files.
3. **No Regressions**: Run compilation, build, or test commands after modifications to ensure stability.

## 🔄 Current State
- **Status**: Active (v1.0.1). Input path with worker queue and throttled re-center, minimize-to-system-tray, file transfers, and portable PyInstaller build.
- **Next Steps**: Maintain and enhance cross-platform or multi-display edge geometries.
