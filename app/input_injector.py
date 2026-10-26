import ctypes
from ctypes import wintypes
import sys

user32 = ctypes.windll.user32

# Win32 Constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

# Win32 Input Structures
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wintypes.DWORD),
        ('wParamL', wintypes.WORD),
        ('wParamH', wintypes.WORD),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [
        ('type', wintypes.DWORD),
        ('u', _INPUT_UNION),
    ]

class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class InputInjector:
    def __init__(self):
        self.input_size = ctypes.sizeof(INPUT)

    def get_cursor_pos(self) -> tuple[int, int]:
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)

    def set_cursor_pos(self, x: int, y: int):
        user32.SetCursorPos(int(x), int(y))

    def move_mouse_rel(self, dx: int, dy: int):
        """Moves mouse relatively by dx and dy pixels cleanly."""
        if dx == 0 and dy == 0:
            return
        # Direct hardware injection with signature extraInfo (0x53484152 = 'SHAR')
        user32.mouse_event(MOUSEEVENTF_MOVE, int(dx), int(dy), 0, 0x53484152)

    def mouse_button(self, button: str, action: str):
        """
        Sends mouse button action.
        button: 'left', 'right', 'middle', 'x1', 'x2'
        action: 'down', 'up'
        """
        btn = button.lower()
        act = action.lower()
        dwFlags = 0
        data = 0

        if btn == 'left':
            dwFlags = MOUSEEVENTF_LEFTDOWN if act == 'down' else MOUSEEVENTF_LEFTUP
        elif btn == 'right':
            dwFlags = MOUSEEVENTF_RIGHTDOWN if act == 'down' else MOUSEEVENTF_RIGHTUP
        elif btn == 'middle':
            dwFlags = MOUSEEVENTF_MIDDLEDOWN if act == 'down' else MOUSEEVENTF_MIDDLEUP
        elif btn in ('x1', 'x2'):
            dwFlags = MOUSEEVENTF_XDOWN if act == 'down' else MOUSEEVENTF_XUP
            data = XBUTTON1 if btn == 'x1' else XBUTTON2
        else:
            return

        user32.mouse_event(dwFlags, 0, 0, data, 0x53484152)

    def mouse_wheel(self, delta: int, horizontal: bool = False):
        """Sends vertical or horizontal scroll wheel input."""
        dwFlags = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
        user32.mouse_event(dwFlags, 0, 0, int(delta), 0x53484152)

    def key_event(self, vk: int, scan: int = 0, flags: int = 0, action: str = 'down'):
        """Synthesizes a keyboard event."""
        vk_code = int(vk) & 0xFFFF
        dwFlags = flags & ~KEYEVENTF_KEYUP
        if action.lower() == 'up':
            dwFlags |= KEYEVENTF_KEYUP

        user32.keybd_event(vk_code, int(scan) & 0xFFFF, dwFlags, 0x53484152)

    def release_all_modifiers(self):
        """Releases Shift, Ctrl, Alt, and Win keys to avoid stuck keys."""
        modifier_vks = [
            0x10, # VK_SHIFT
            0x11, # VK_CONTROL
            0x12, # VK_MENU (Alt)
            0x5B, # VK_LWIN
            0x5C, # VK_RWIN
        ]
        for vk in modifier_vks:
            self.key_event(vk, action='up')

injector = InputInjector()
