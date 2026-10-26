# -*- mode: python ; coding: utf-8 -*-
# Build: python -m PyInstaller ShareLink.spec --noconfirm
# Output: dist/ShareLink_DuoCast.exe (portable, writes config.json beside itself)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("web", "web")],
    hiddenimports=[
        # uvicorn resolves these by string at runtime
        "uvicorn.logging",
        "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
        "uvicorn.protocols", "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan", "uvicorn.lifespan.on",
        # platform backends picked dynamically
        "webview.platforms.edgechromium",
        "pystray._win32",
    ],
    excludes=["tkinter", "numpy", "pandas", "matplotlib", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ShareLink_DuoCast",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon="web/icon.ico",
)
