"""
supermouse_launcher.py — Super Mouse PC companion
Requires: pip install pyserial requests

Usage:
    python supermouse_launcher.py          # auto-detect COM port
    python supermouse_launcher.py COM3     # specify port manually
"""

import sys
import os
import json
import time
import subprocess
import datetime
import ctypes
from ctypes import wintypes
import uuid
import threading
import re
import urllib.parse
import urllib.request
import difflib
import smtplib
from email.message import EmailMessage
from pathlib import Path
import serial
import serial.tools.list_ports

try:
    from PIL import Image, ImageOps
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("WARNING: 'requests' not installed. Weather disabled. Run: pip install requests")

try:
    import speech_recognition as sr
    HAS_SPEECH = True
except ImportError:
    sr = None
    HAS_SPEECH = False

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    sd = None
    HAS_SOUNDDEVICE = False

# ── App launcher map (index matches APPS[] in lvgl_ui.cpp) ──────────
CONFIG_PATH = Path(__file__).with_name("supermouse_config.json")
NOTES_PATH = Path(__file__).with_name("supermouse_notes.md")

DEFAULT_CONFIG = {
    "background": {"color": "181A24", "image": "aurora"},
    "weather": {"location": "", "units": "m"},
    "keyboard_words": {},
    "keyboard_pairs": {},
    "assistant": {"notes": [], "reminders": [], "alarms": [], "timers": []},
    "email_summary": {
        "enabled": False,
        "daily_time": "18:00",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "from": "",
        "to": "",
        "last_sent_date": "",
    },
    "widgets": [
        {"title": "Clock", "line1": "Auto time", "line2": "Weather"},
        {"title": "PC", "line1": "Photos", "line2": "Files ready"},
    ],
    "apps": [
        {"name": "Chrome", "abbr": "G", "color": "4285F4", "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe"},
        {"name": "Word", "abbr": "W", "color": "2B579A", "path": "winword"},
        {"name": "Excel", "abbr": "X", "color": "217346", "path": "excel"},
        {"name": "PowerPnt", "abbr": "P", "color": "D04423", "path": "powerpnt"},
        {"name": "VS Code", "abbr": "VS", "color": "007ACC", "path": "code"},
        {"name": "Settings", "abbr": "S", "color": "636366", "path": "ms-settings:"},
    ],
}


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return DEFAULT_CONFIG

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"[config] failed to read {CONFIG_PATH.name}: {e}")
        return DEFAULT_CONFIG

    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    merged["apps"] = (cfg.get("apps") or DEFAULT_CONFIG["apps"])[:6]
    while len(merged["apps"]) < 6:
        merged["apps"].append(DEFAULT_CONFIG["apps"][len(merged["apps"])])
    merged["widgets"] = (cfg.get("widgets") or DEFAULT_CONFIG["widgets"])[:2]
    while len(merged["widgets"]) < 2:
        merged["widgets"].append(DEFAULT_CONFIG["widgets"][len(merged["widgets"])])
    merged["keyboard_words"] = cfg.get("keyboard_words") or {}
    merged["keyboard_pairs"] = cfg.get("keyboard_pairs") or {}
    merged["assistant"] = cfg.get("assistant") or {"notes": [], "reminders": [], "alarms": [], "timers": []}
    email_cfg = dict(DEFAULT_CONFIG["email_summary"])
    email_cfg.update(cfg.get("email_summary") or {})
    merged["email_summary"] = email_cfg
    return merged


CONFIG = load_config()
APPS = CONFIG["apps"]
KEYBOARD_WORDS = CONFIG.get("keyboard_words", {})
KEYBOARD_PAIRS = CONFIG.get("keyboard_pairs", {})
SCREEN_W = 320
SCREEN_H = 480
BG_RAW_SIZE = SCREEN_W * SCREEN_H * 2

# Media tap codes (must match lvgl_ui.cpp)
TAP_PLAY_PAUSE = 20
TAP_PREV       = 21
TAP_NEXT       = 22
TAP_MUTE       = 32
TAP_VOICE_START = 33
TAP_VOICE_STOP  = 34
TAP_ASSIST_START = 35
TAP_ASSIST_STOP  = 36

pc_volume_estimate = 70
pc_volume_synced = False
volume_controller = None
volume_controller_failed = False
SERIAL_LOCK = threading.Lock()
MIC_STREAM_LOCK = threading.Lock()
voice_typing_enabled = False
voice_type_next_utterance = False
voice_text_context_until = 0.0
last_voice_text = ""
last_calc_result = None
microphone_warmed = False
WEATHER_TIMEOUT = 3

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
VK_CONTROL = 0x11
VK_V = 0x56
VK_SHIFT = 0x10
VK_MENU = 0x12
VK_RETURN = 0x0D
VK_BACK = 0x08
VK_DELETE = 0x2E
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_INSERT = 0x2D
VK_F4 = 0x73
VK_F1 = 0x70
VK_F2 = 0x71
VK_F3 = 0x72
VK_F5 = 0x74
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_F11 = 0x7A
VK_F12 = 0x7B
VK_LWIN = 0x5B
VK_SNAPSHOT = 0x2C
VK_0 = 0x30
VK_1 = 0x31
VK_2 = 0x32
VK_3 = 0x33
VK_4 = 0x34
VK_5 = 0x35
VK_6 = 0x36
VK_7 = 0x37
VK_8 = 0x38
VK_9 = 0x39
VK_A = 0x41
VK_B = 0x42
VK_C = 0x43
VK_D = 0x44
VK_E = 0x45
VK_F = 0x46
VK_G = 0x47
VK_H = 0x48
VK_I = 0x49
VK_L = 0x4C
VK_M = 0x4D
VK_N = 0x4E
VK_O = 0x4F
VK_P = 0x50
VK_Q = 0x51
VK_R = 0x52
VK_S = 0x53
VK_T = 0x54
VK_U = 0x55
VK_W = 0x57
VK_X = 0x58
VK_Y = 0x59
VK_Z = 0x5A
VK_OEM_PLUS = 0xBB
VK_OEM_MINUS = 0xBD
KEYEVENTF_KEYUP = 0x0002
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
SW_RESTORE = 9

CLSCTX_ALL = 0x17
E_RENDER = 0
E_CONSOLE = 0


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", RECT),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, value):
        u = uuid.UUID(value)
        bytes_le = u.bytes_le
        self.Data1 = int.from_bytes(bytes_le[0:4], "little")
        self.Data2 = int.from_bytes(bytes_le[4:6], "little")
        self.Data3 = int.from_bytes(bytes_le[6:8], "little")
        self.Data4 = (ctypes.c_ubyte * 8).from_buffer_copy(bytes_le[8:16])


CLSID_MMDEVICE_ENUMERATOR = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDEVICE_ENUMERATOR = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAUDIO_ENDPOINT_VOLUME = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")


def check_hr(hr, where):
    if hr < 0:
        raise OSError(f"{where} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")


def com_method(obj, index, restype, *argtypes):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


class WindowsVolume:
    def __init__(self):
        ctypes.oledll.ole32.CoInitialize(None)

        self.enumerator = ctypes.c_void_p()
        hr = ctypes.oledll.ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDEVICE_ENUMERATOR),
            None,
            CLSCTX_ALL,
            ctypes.byref(IID_IMMDEVICE_ENUMERATOR),
            ctypes.byref(self.enumerator),
        )
        check_hr(hr, "CoCreateInstance")

        self.device = ctypes.c_void_p()
        get_default = com_method(
            self.enumerator,
            4,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )
        check_hr(get_default(self.enumerator, E_RENDER, E_CONSOLE, ctypes.byref(self.device)),
                 "GetDefaultAudioEndpoint")

        self.endpoint = ctypes.c_void_p()
        activate = com_method(
            self.device,
            3,
            ctypes.c_long,
            ctypes.POINTER(GUID),
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        check_hr(activate(self.device, ctypes.byref(IID_IAUDIO_ENDPOINT_VOLUME), CLSCTX_ALL, None,
                          ctypes.byref(self.endpoint)),
                 "Activate IAudioEndpointVolume")

        self._set_scalar = com_method(
            self.endpoint, 7, ctypes.c_long, ctypes.c_float, ctypes.c_void_p
        )
        self._get_scalar = com_method(
            self.endpoint, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_float)
        )

    def set_percent(self, value):
        scalar = max(0.0, min(1.0, float(value) / 100.0))
        check_hr(self._set_scalar(self.endpoint, ctypes.c_float(scalar), None),
                 "SetMasterVolumeLevelScalar")

    def get_percent(self):
        scalar = ctypes.c_float()
        check_hr(self._get_scalar(self.endpoint, ctypes.byref(scalar)),
                 "GetMasterVolumeLevelScalar")
        return int(round(scalar.value * 100))


def find_port():
    """Auto-detect the ESP32 USB CDC port."""
    candidates = []
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").upper()
        if any(k in desc for k in ("USB", "JTAG", "CP210", "CH34", "SERIAL")):
            candidates.append(p.device)
    if candidates:
        return candidates[0]
    ports = serial.tools.list_ports.comports()
    return ports[0].device if ports else None


def launch_app(index):
    """Open the app at the given index."""
    if index < 0 or index >= len(APPS):
        print(f"[launcher] Unknown app index {index}")
        return
    app = APPS[index]
    path = app["path"]
    if path == "__touchpad__":
        print("[launcher] Touchpad app runs on the ESP32.")
        return
    print(f"[launcher] Opening: {app['name']}")
    if str(app.get("name", "")).lower() == "chrome":
        if focus_or_launch_chrome():
            return

    try:
        if path.startswith("ms-"):
            os.startfile(path)
        elif path in ("winword", "excel", "powerpnt", "code"):
            subprocess.Popen(["start", path], shell=True)
        elif app["name"] == "Chrome" and not os.path.exists(path):
            subprocess.Popen(["start", "chrome"], shell=True)
        else:
            subprocess.Popen([path])
    except Exception as e:
        # Fallback: try shell start
        try:
            subprocess.Popen(f'start "" "{path}"', shell=True)
        except Exception as e2:
            print(f"[launcher] Failed to open {app['name']}: {e2}")


def send_media_key(code):
    """Send a Windows media virtual key."""
    vk = {
        "PlayPause": 0xB3,
        "PreviousTrack": 0xB1,
        "NextTrack": 0xB0,
        "Mute": 0xAD,
        "VolumeDown": 0xAE,
        "VolumeUp": 0xAF,
    }
    key_name = {
        TAP_PLAY_PAUSE: "PlayPause",
        TAP_PREV: "PreviousTrack",
        TAP_NEXT: "NextTrack",
        TAP_MUTE: "Mute",
    }.get(code)
    if key_name is None:
        return

    try:
        vk_code = vk[key_name]
        user32 = ctypes.windll.user32
        user32.keybd_event(vk_code, 0, 0, 0)
        user32.keybd_event(vk_code, 0, 2, 0)
    except Exception as e:
        print(f"[media] key send failed: {e}")


def send_volume_key(up):
    """Press the Windows volume up/down virtual key once."""
    key = 0xAF if up else 0xAE
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, 2, 0)
    except Exception as e:
        print(f"[volume] key send failed: {e}")


def tap_key(vk_code):
    """Tap one Windows virtual key."""
    user32 = ctypes.windll.user32
    user32.keybd_event(vk_code, 0, 0, 0)
    user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)


def press_hotkey(*keys):
    """Press a Windows hotkey chord."""
    user32 = ctypes.windll.user32
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)


def key_code_for_char(ch):
    """Return a Windows virtual-key code for common ribbon letters/digits."""
    ch = str(ch).upper()
    if len(ch) == 1 and "A" <= ch <= "Z":
        return ord(ch)
    if len(ch) == 1 and "0" <= ch <= "9":
        return ord(ch)
    if ch in ("=", "+"):
        return VK_OEM_PLUS
    return None


def tap_chars(chars, delay=0.05):
    """Tap a sequence of keyboard characters by virtual-key code."""
    for ch in chars:
        code = key_code_for_char(ch)
        if code is not None:
            tap_key(code)
            time.sleep(delay)


def press_ribbon_sequence(*keys):
    """Drive Office ribbon shortcuts, e.g. Alt, H, M, C."""
    tap_key(VK_MENU)
    time.sleep(0.08)
    tap_chars(keys, delay=0.08)


def key_name_to_vk(name):
    """Map spoken key names to virtual-key codes."""
    clean = normalize_voice_command(name).replace("key", "").strip()
    aliases = {
        "enter": VK_RETURN, "return": VK_RETURN,
        "tab": VK_TAB, "escape": VK_ESCAPE, "esc": VK_ESCAPE,
        "space": VK_SPACE, "backspace": VK_BACK, "delete": VK_DELETE,
        "home": VK_HOME, "end": VK_END, "page up": VK_PRIOR,
        "page down": VK_NEXT, "insert": VK_INSERT,
        "up": VK_UP, "down": VK_DOWN, "left": VK_LEFT, "right": VK_RIGHT,
        "arrow up": VK_UP, "arrow down": VK_DOWN,
        "arrow left": VK_LEFT, "arrow right": VK_RIGHT,
    }
    if clean in aliases:
        return aliases[clean]
    f_match = re.match(r"^f\s*(\d{1,2})$", clean)
    if f_match:
        num = int(f_match.group(1))
        if 1 <= num <= 12:
            return VK_F1 + num - 1
    if len(clean) == 1:
        return key_code_for_char(clean)
    return None


def activate_window_by_title(*keywords):
    """Bring the first visible window containing any keyword to the foreground."""
    user32 = ctypes.windll.user32
    keywords = tuple(str(k).lower() for k in keywords if k)
    if not keywords:
        return False

    found = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def enum_proc(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.lower()
        if any(keyword in title for keyword in keywords):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(EnumWindowsProc(enum_proc), 0)
    if not found:
        return False

    hwnd = found[0]
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    return True


def activate_excel_window():
    """Bring Excel to the foreground if it is already open."""
    return activate_window_by_title("excel")


def focus_or_launch_chrome():
    """Restore an existing Chrome window, or launch Chrome if none is open."""
    if activate_window_by_title("google chrome", " - chrome", "chrome"):
        return True
    try:
        subprocess.Popen([chrome_command()])
        return True
    except Exception as e:
        print(f"[launcher] Chrome focus/launch failed: {e}")
        try:
            subprocess.Popen(["start", "chrome"], shell=True)
            return True
        except Exception as inner:
            print(f"[launcher] Chrome fallback failed: {inner}")
            return False


def focus_or_launch_calculator():
    """Restore Calculator or launch it."""
    if activate_window_by_title("calculator"):
        return True
    for command in (
        ["calc.exe"],
        ["cmd", "/c", "start", "", "calculator:"],
        ["cmd", "/c", "start", "", "calc"],
    ):
        try:
            subprocess.Popen(command)
            time.sleep(0.6)
            activate_window_by_title("calculator")
            return True
        except Exception as e:
            print(f"[calc] launch attempt failed: {e}")
    return False


def focus_or_launch_camera():
    """Restore Windows Camera or launch it."""
    if activate_window_by_title("camera"):
        return True
    for command in (
        ["cmd", "/c", "start", "", "microsoft.windows.camera:"],
        ["cmd", "/c", "start", "", "camera:"],
    ):
        try:
            subprocess.Popen(command)
            time.sleep(1.5)
            activate_window_by_title("camera")
            return True
        except Exception as e:
            print(f"[camera] launch attempt failed: {e}")
    return False


def take_camera_photo():
    """Trigger the Windows Camera shutter."""
    # A running Camera app may be left in video mode. Reopening normally returns
    # to photo mode on Windows Camera, which avoids starting a video recording.
    close_named_app("camera")
    time.sleep(0.5)
    if not focus_or_launch_camera():
        return False
    time.sleep(2.0)
    tap_key(VK_SPACE)
    return True


def handle_camera_command(ser, spoken):
    """Handle camera and selfie voice commands."""
    if re.search(r"\b(?:close|quit|exit|stop)\s+(?:the\s+)?camera\b", spoken):
        if close_named_app("camera"):
            send_voice_status(ser, "Closed", "Camera")
        else:
            send_voice_status(ser, "Close failed", "Camera")
        return True

    has_camera = re.search(r"\b(?:camera|selfie|photo|picture)\b", spoken) is not None
    wants_capture = re.search(r"\b(?:take|capture|click|shoot)\b", spoken) is not None
    wants_open = re.search(r"\b(?:open|launch|start)\s+(?:the\s+)?camera\b", spoken) is not None

    if has_camera and wants_capture:
        if take_camera_photo():
            send_voice_status(ser, "Camera", "Photo taken")
        else:
            send_voice_status(ser, "Camera failed", "Check app")
        return True

    if wants_open:
        if focus_or_launch_camera():
            send_voice_status(ser, "Opening", "Camera")
        else:
            send_voice_status(ser, "Open failed", "Camera")
        return True

    return False


def open_print_dialog(auto_confirm=False):
    """Open the active app's print dialog, optionally confirm it."""
    press_hotkey(VK_CONTROL, VK_P)
    time.sleep(1.0)
    if auto_confirm:
        tap_key(VK_RETURN)
    return True


def set_volume_target_fallback(target):
    """Move Windows volume toward target using media volume keys."""
    global pc_volume_estimate, pc_volume_synced

    target = max(0, min(100, int(target)))
    if not pc_volume_synced:
        for _ in range(55):
            send_volume_key(False)
            time.sleep(0.004)
        pc_volume_estimate = 0
        pc_volume_synced = True

    delta = target - pc_volume_estimate
    steps = min(55, abs(delta) // 2)
    if steps == 0 and delta != 0:
        steps = 1

    for _ in range(steps):
        send_volume_key(delta > 0)
        time.sleep(0.01)

    pc_volume_estimate = target
    print(f"[volume] fallback target {target}%")


def set_volume_target(target):
    """Set Windows master volume to an exact 0-100 percent target."""
    global volume_controller, volume_controller_failed, pc_volume_estimate

    target = max(0, min(100, int(target)))
    if not volume_controller_failed:
        try:
            if volume_controller is None:
                volume_controller = WindowsVolume()
                pc_volume_estimate = volume_controller.get_percent()
                print(f"[volume] Windows audio API ready, current {pc_volume_estimate}%")
            volume_controller.set_percent(target)
            pc_volume_estimate = target
            print(f"[volume] set {target}%")
            return
        except Exception as e:
            volume_controller_failed = True
            print(f"[volume] exact volume failed, using key fallback: {e}")

    set_volume_target_fallback(target)


def change_volume(delta):
    """Adjust Windows master volume by a relative percent amount."""
    global volume_controller, volume_controller_failed, pc_volume_estimate

    if not volume_controller_failed:
        try:
            if volume_controller is None:
                volume_controller = WindowsVolume()
            current = volume_controller.get_percent()
            set_volume_target(current + delta)
            return
        except Exception as e:
            volume_controller_failed = True
            print(f"[volume] exact relative volume failed, using estimate: {e}")

    set_volume_target(pc_volume_estimate + delta)


def get_brightness_percent():
    """Read internal display brightness when Windows exposes it."""
    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | Select-Object -First 1).CurrentBrightness",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
        if result.returncode == 0:
            match = re.search(r"\d{1,3}", result.stdout)
            if match:
                return max(0, min(100, int(match.group(0))))
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            print(f"[brightness] read failed: {detail}")
    except Exception as e:
        print(f"[brightness] read failed: {e}")
    return None


def set_brightness_target(target):
    """Set internal display brightness using Windows WMI/CIM."""
    target = max(0, min(100, int(target)))
    try:
        ps = (
            "$m=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | Select-Object -First 1; "
            f"if($m){{Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{{Timeout=1;Brightness={target}}}}}"
        )
        result = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"[brightness] set {target}%")
            return True
        detail = (result.stderr or result.stdout or "").strip()
        print(f"[brightness] set failed: {detail or result.returncode}")
    except Exception as e:
        print(f"[brightness] set failed: {e}")
    return False


def change_brightness(delta):
    current = get_brightness_percent()
    if current is None:
        return False, None
    target = max(0, min(100, current + int(delta)))
    return set_brightness_target(target), target


def set_power_saver(enabled):
    """Switch Windows power plan between Power saver and Balanced."""
    preferred_scheme = "SCHEME_MAX" if enabled else "SCHEME_BALANCED"
    fallback_name = "power saver" if enabled else "balanced"

    def activate_scheme(scheme):
        return subprocess.run(
            ["powercfg", "/setactive", scheme],
            capture_output=True,
            text=True,
            timeout=5,
        )

    try:
        result = activate_scheme(preferred_scheme)
        if result.returncode == 0:
            print(f"[power] {'power saver' if enabled else 'balanced'} enabled")
            return True

        list_result = subprocess.run(
            ["powercfg", "/list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if list_result.returncode == 0:
            for line in list_result.stdout.splitlines():
                if fallback_name in line.lower():
                    match = re.search(r"([0-9a-fA-F-]{36})", line)
                    if match:
                        result = activate_scheme(match.group(1))
                        if result.returncode == 0:
                            print(f"[power] {fallback_name} enabled by GUID")
                            return True

        detail = (result.stderr or result.stdout or "").strip()
        print(f"[power] powercfg failed: {detail or result.returncode}")
    except Exception as e:
        print(f"[power] power saver failed: {e}")
    return False


def active_power_scheme_guid():
    """Return the current Windows power scheme GUID, if available."""
    try:
        result = subprocess.run(
            ["powercfg", "/getactivescheme"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            match = re.search(r"([0-9a-fA-F-]{36})", result.stdout)
            if match:
                return match.group(1)
    except Exception as e:
        print(f"[power] active scheme read failed: {e}")
    return None


def set_windows_energy_saver(enabled):
    """Enable Windows Energy saver policy for the active power scheme."""
    scheme = active_power_scheme_guid() or "SCHEME_CURRENT"
    policy = "1" if enabled else "0"
    threshold = "100" if enabled else "20"
    commands = (
        ["powercfg", "/setacvalueindex", scheme, "SUB_ENERGYSAVER", "ESPOLICY", policy],
        ["powercfg", "/setdcvalueindex", scheme, "SUB_ENERGYSAVER", "ESPOLICY", policy],
        ["powercfg", "/setacvalueindex", scheme, "SUB_ENERGYSAVER", "ESBATTTHRESHOLD", threshold],
        ["powercfg", "/setdcvalueindex", scheme, "SUB_ENERGYSAVER", "ESBATTTHRESHOLD", threshold],
        ["powercfg", "/setactive", scheme],
    )
    try:
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                print(f"[power] energy saver command failed: {' '.join(command)} -> {detail or result.returncode}")
                return False
        print(f"[power] Windows Energy saver {'enabled' if enabled else 'disabled'}")
        return True
    except Exception as e:
        print(f"[power] Windows Energy saver failed: {e}")
        return False


def move_mouse(dx, dy):
    """Move the Windows mouse cursor by a relative amount."""
    try:
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, int(dx), int(dy), 0, 0)
    except Exception as e:
        print(f"[mouse] move failed: {e}")


def click_mouse(button):
    """Click the left button for 0, right button for 1."""
    try:
        user32 = ctypes.windll.user32
        if int(button) == 1:
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        else:
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    except Exception as e:
        print(f"[mouse] click failed: {e}")


def scroll_mouse(notches):
    """Scroll the focused window. Positive is up, negative is down."""
    try:
        delta = int(notches) * 120
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        print(f"[mouse] scroll {notches}")
    except Exception as e:
        print(f"[mouse] scroll failed: {e}")


def set_clipboard_text(text):
    """Put Unicode text on the Windows clipboard."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_bool
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.EmptyClipboard.restype = ctypes.c_bool
    user32.CloseClipboard.restype = ctypes.c_bool

    data = str(text)
    byte_count = (len(data) + 1) * ctypes.sizeof(ctypes.c_wchar)
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, byte_count)
    if not handle:
        raise OSError("GlobalAlloc failed")

    locked = kernel32.GlobalLock(handle)
    if not locked:
        kernel32.GlobalFree(handle)
        raise OSError("GlobalLock failed")

    ctypes.memmove(locked, ctypes.create_unicode_buffer(data), byte_count)
    kernel32.GlobalUnlock(handle)

    opened = False
    for _ in range(8):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.025)
    if not opened:
        kernel32.GlobalFree(handle)
        raise OSError("OpenClipboard failed")

    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise OSError("SetClipboardData failed")
        handle = None
    finally:
        user32.CloseClipboard()


def paste_text(text):
    """Paste text into the focused PC app."""
    try:
        set_clipboard_text(text)
        time.sleep(0.06)
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_V, 0, 0, 0)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        print(f"[typing] pasted: {text.strip()}")
    except Exception as e:
        print(f"[typing] paste failed: {e}")


SPEECH_COMMAND_REPLACEMENTS = [
    (r"\bgoogle chrome\b", "chrome"),
    (r"\bchrome browser\b", "chrome"),
    (r"\bcrone\b", "chrome"),
    (r"\bcrome\b", "chrome"),
    (r"\bchrom\b", "chrome"),
    (r"\bmicro soft word\b", "word"),
    (r"\bms word\b", "word"),
    (r"\bexcel\b", "excel"),
    (r"\bexel\b", "excel"),
    (r"\bwork book\b", "workbook"),
    (r"\bblankwork\s*book\b", "blank workbook"),
    (r"\bblank work book\b", "blank workbook"),
    (r"\bnew blankworkbook\b", "new blank workbook"),
    (r"\bnew blank work book\b", "new blank workbook"),
    (r"\bpower point\b", "powerpoint"),
    (r"\bpowerpnt\b", "powerpoint"),
    (r"\bvs code\b", "visual studio code"),
    (r"\bwe as code\b", "visual studio code"),
    (r"\bvs cold\b", "visual studio code"),
    (r"\bvisual studio cold\b", "visual studio code"),
    (r"\bnew taps\b", "new tabs"),
    (r"\bnew tabes\b", "new tabs"),
    (r"\bten new tap\b", "ten new tabs"),
    (r"\blast tap\b", "last tab"),
    (r"\bsearcher\b", "search"),
    (r"\bsearch in\b", "search"),
    (r"\bsearch on\b", "search"),
    (r"\bsearch about\b", "search for"),
    (r"\blookout\b", "look up"),
    (r"\bgo do\b", "go to"),
    (r"\bgo too\b", "go to"),
    (r"\bgo two\b", "go to"),
    (r"\btype in\b", "type"),
    (r"\btyping\s+(?=hello|this|the|a|an|my|your|in)\b", "type "),
    (r"\bright\s+(?=this|the|a|an|my|your|in|down|up)\b", "write "),
    (r"\bdictation own\b", "dictation on"),
    (r"\bdictation of\b", "dictation off"),
    (r"\bback space\b", "backspace"),
    (r"\bselect doll\b", "select all"),
    (r"\bscreen short\b", "screenshot"),
    (r"\bscreen shut\b", "screenshot"),
    (r"\bscreen shot\b", "screenshot"),
    (r"\btake screen\b", "take screenshot"),
    (r"\bclose software\b", "close app"),
    (r"\bclose application\b", "close app"),
    (r"\bclose program\b", "close app"),
    (r"\bminimise\b", "minimize"),
    (r"\bmaximise\b", "maximize"),
    (r"\bvolume app\b", "volume up"),
    (r"\bwalume\b", "volume"),
    (r"\bmute sound\b", "mute"),
    (r"\bun mute\b", "unmute"),
    (r"\bmultiplied in\b", "multiplied by"),
    (r"\bmultiplied with\b", "multiplied by"),
    (r"\bmultiply with\b", "multiply by"),
    (r"\bmultiplicate\b", "multiply"),
    (r"\bmultiplication\b", "multiplication"),
    (r"\basterix\b", "asterisk"),
    (r"\bastericks\b", "asterisk"),
    (r"\bstar mark\b", "star"),
    (r"\binto\b", "x"),
    (r"\bdivide with\b", "divide by"),
    (r"\bdivided with\b", "divided by"),
    (r"\barctic fox\b", "arcticfox"),
    (r"\barctic fox dot com\b", "arcticfox.com"),
    (r"\bdot\s+com\b", ".com"),
    (r"\bdot\s+in\b", ".in"),
    (r"\bdot\s+org\b", ".org"),
    (r"\bdot\s+net\b", ".net"),
]


def normalize_voice_command(text):
    """Normalize common speech-recognition mistakes before command parsing."""
    spoken = " ".join(str(text).lower().strip().split())
    spoken = spoken.replace(" . ", ".").replace(" dot ", " dot ")
    for _ in range(2):
        for pattern, repl in SPEECH_COMMAND_REPLACEMENTS:
            spoken = re.sub(pattern, repl, spoken)
    spoken = re.sub(r"\s+([.])\s+", r"\1", spoken)
    spoken = re.sub(r"\s+([.])", r"\1", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    return spoken


def voice_text_to_typing(text):
    """Clean common dictated punctuation words."""
    words = str(text).strip().split()
    output = []
    punctuation = {
        "period": ".",
        "full stop": ".",
        "dot": ".",
        "comma": ",",
        "question mark": "?",
        "question": "?",
        "exclamation mark": "!",
        "exclamation": "!",
        "colon": ":",
        "semicolon": ";",
        "slash": "/",
        "backslash": "\\",
        "dash": "-",
        "hyphen": "-",
        "underscore": "_",
        "at sign": "@",
        "at the rate": "@",
        "ampersand": "&",
    }

    i = 0
    while i < len(words):
        two = " ".join(words[i:i + 2]).lower()
        one = words[i].lower()
        three = " ".join(words[i:i + 3]).lower()
        if two == "new line" or two == "next line":
            output.append("\n")
            i += 2
        elif two == "new paragraph":
            output.append("\n\n")
            i += 2
        elif three in punctuation:
            if output and not output[-1].endswith(("\n", " ")):
                output[-1] = output[-1].rstrip()
            output.append(punctuation[three])
            i += 3
        elif one == "enter":
            output.append("\n")
            i += 1
        elif one == "space":
            output.append(" ")
            i += 1
        elif two in punctuation:
            if output and not output[-1].endswith(("\n", " ")):
                output[-1] = output[-1].rstrip()
            output.append(punctuation[two])
            i += 2
        elif one in punctuation:
            if output and not output[-1].endswith(("\n", " ")):
                output[-1] = output[-1].rstrip()
            output.append(punctuation[one])
            i += 1
        else:
            output.append(words[i])
            i += 1

    text_out = " ".join(output)
    text_out = text_out.replace(" \n ", "\n").replace(" \n", "\n").replace("\n ", "\n")
    for mark in (".", ",", "?", "!", ":", ";"):
        text_out = text_out.replace(f" {mark}", mark)
    for mark in ("@", "/", "\\", "_"):
        text_out = text_out.replace(f" {mark} ", mark).replace(f" {mark}", mark).replace(f"{mark} ", mark)
    text_out = re.sub(r"(?<=\w)\.\s+(?=\w{2,4}\b)", ".", text_out)
    return text_out


def grammar_correct_typed_text(text):
    """Apply lightweight offline grammar cleanup for dictated text only."""
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        return cleaned

    replacements = (
        (r"\bi\b", "I"),
        (r"\bim\b", "I'm"),
        (r"\bi m\b", "I'm"),
        (r"\bive\b", "I've"),
        (r"\bi ve\b", "I've"),
        (r"\bid\b", "I'd"),
        (r"\bi d\b", "I'd"),
        (r"\bill\b", "I'll"),
        (r"\bi ll\b", "I'll"),
        (r"\bdont\b", "don't"),
        (r"\bdoesnt\b", "doesn't"),
        (r"\bdidnt\b", "didn't"),
        (r"\bcant\b", "can't"),
        (r"\bcannot able to\b", "cannot"),
        (r"\bcould not able to\b", "could not"),
        (r"\bcouldn't able to\b", "couldn't"),
        (r"\bcan able to\b", "can"),
        (r"\bwas not able\b", "was not able"),
        (r"\bwont\b", "won't"),
        (r"\bisnt\b", "isn't"),
        (r"\barent\b", "aren't"),
        (r"\bwasnt\b", "wasn't"),
        (r"\bwerent\b", "weren't"),
        (r"\bthats\b", "that's"),
        (r"\bwhats\b", "what's"),
        (r"\blets\b", "let's"),
        (r"\bi wants\b", "I want"),
        (r"\bi want to\b", "I want to"),
        (r"\bi wanted to\b", "I wanted to"),
        (r"\beverything are\b", "everything is"),
        (r"\beverything is fine\b", "everything is fine"),
        (r"\bhope everything\b", "I hope everything"),
        (r"\baround at\b", "around"),
        (r"\bhow to be proceed\b", "how to proceed"),
    )
    for pattern, repl in replacements:
        cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(
        r"^how are you and I hope\b",
        "How are you? I hope",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^how are you and hope\b",
        "How are you? I hope",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^how are you doing and I hope\b",
        "How are you doing? I hope",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^how are you doing and hope\b",
        "How are you doing? I hope",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^what are you doing and I hope\b",
        "What are you doing? I hope",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^what are you doing and hope\b",
        "What are you doing? I hope",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\ba ([aeiouAEIOU]\w*)\b", r"an \1", cleaned)
    cleaned = re.sub(r"\ban (user|university|usual|one)\b", r"a \1", cleaned, flags=re.IGNORECASE)

    def cap_sentence(match):
        return match.group(1) + match.group(2).upper()

    cleaned = cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned
    cleaned = re.sub(r"(^|[.!?]\s+)([a-z])", cap_sentence, cleaned)
    cleaned = re.sub(r"\bi\b", "I", cleaned)

    if len(cleaned.split()) > 2 and not re.search(r"[.!?]$", cleaned):
        cleaned += "."
    return cleaned


def polish_assistant_question(spoken):
    """Clean common speech/grammar slips before AI assistant search."""
    cleaned = re.sub(r"\s+", " ", str(spoken)).strip()
    if not cleaned:
        return cleaned

    replacements = (
        (r"^what'?s\s+the\s+when\s+is\b", "when is"),
        (r"^what\s+is\s+the\s+when\s+is\b", "when is"),
        (r"^what'?s\s+the\s+where\s+is\b", "where is"),
        (r"^what\s+is\s+the\s+where\s+is\b", "where is"),
        (r"^what'?s\s+the\s+who\s+is\b", "who is"),
        (r"^what\s+is\s+the\s+who\s+is\b", "who is"),
        (r"^what'?s\s+the\s+what\s+is\b", "what is"),
        (r"^what\s+is\s+the\s+what\s+is\b", "what is"),
        (r"\bcan you able to\b", "can you"),
        (r"\bcan able to\b", "can"),
        (r"\bcould you able to\b", "could you"),
        (r"\bcould able to\b", "could"),
        (r"\bnext f one race\b", "next F1 race"),
        (r"\bnext formula one race\b", "next F1 race"),
        (r"\bf one\b", "F1"),
        (r"\bformula one\b", "F1"),
    )
    for pattern, repl in replacements:
        cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\bwhen is the next F1 race\b", "when is the next F1 race", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;")
    return cleaned


def prepare_voice_typing_text(text):
    """Convert dictated text into polished text before pasting."""
    return grammar_correct_typed_text(voice_text_to_typing(text))


def focused_window_class(hwnd):
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(128)
    try:
        ctypes.windll.user32.GetClassNameW(hwnd, buf, len(buf))
    except Exception:
        return ""
    return buf.value


def focused_text_input_info():
    """Best-effort Windows check for an active edit/text field."""
    try:
        user32 = ctypes.windll.user32
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False, ""

        thread_id = user32.GetWindowThreadProcessId(foreground, None)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return False, focused_window_class(foreground)

        focus_class = focused_window_class(info.hwndFocus).lower()
        caret_class = focused_window_class(info.hwndCaret).lower()
        classes = " ".join(part for part in (focus_class, caret_class) if part)
        text_class_tokens = (
            "edit", "richedit", "text", "textbox", "msctls_statusbar",
            "internet explorer_server",
        )
        if any(token in classes for token in text_class_tokens):
            return True, classes

        caret_w = int(info.rcCaret.right - info.rcCaret.left)
        caret_h = int(info.rcCaret.bottom - info.rcCaret.top)
        if info.hwndCaret and caret_h > 3 and caret_w >= 0:
            return True, classes or "caret"

        return False, classes
    except Exception as e:
        print(f"[voice] text focus check failed: {e}")
        return False, ""


def handle_auto_text_field_command(ser, spoken):
    """Allow basic editing keys while automatic text-field dictation is active."""
    if is_voice_phrase(spoken, "enter", "press enter", "new line"):
        tap_key(VK_RETURN)
        send_voice_status(ser, "Key", "Enter")
        return True
    if is_voice_phrase(spoken, "backspace", "delete last", "delete last character"):
        tap_key(VK_BACK)
        send_voice_status(ser, "Key", "Backspace")
        return True
    if is_voice_phrase(spoken, "delete", "press delete"):
        tap_key(VK_DELETE)
        send_voice_status(ser, "Key", "Delete")
        return True
    if is_voice_phrase(spoken, "space", "press space"):
        tap_key(VK_SPACE)
        send_voice_status(ser, "Key", "Space")
        return True
    if is_voice_phrase(spoken, "tab", "press tab"):
        tap_key(VK_TAB)
        send_voice_status(ser, "Key", "Tab")
        return True
    if is_voice_phrase(spoken, "select all"):
        press_hotkey(VK_CONTROL, VK_A)
        send_voice_status(ser, "Edit", "Select all")
        return True
    if is_voice_phrase(spoken, "copy", "copy selected"):
        press_hotkey(VK_CONTROL, VK_C)
        send_voice_status(ser, "Edit", "Copy")
        return True
    if is_voice_phrase(spoken, "paste", "paste here"):
        press_hotkey(VK_CONTROL, VK_V)
        send_voice_status(ser, "Edit", "Paste")
        return True
    if is_voice_phrase(spoken, "cut", "cut selected"):
        press_hotkey(VK_CONTROL, VK_X)
        send_voice_status(ser, "Edit", "Cut")
        return True
    return False


def weather_code_text(code):
    """Map Open-Meteo weather codes to short display text."""
    codes = {
        0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Cloudy",
        45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
        56: "Freezing drizzle", 57: "Freezing drizzle", 61: "Rain",
        63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
        71: "Snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
        80: "Rain showers", 81: "Rain showers", 82: "Heavy showers",
        85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm",
        96: "Thunderstorm", 99: "Thunderstorm",
    }
    return codes.get(int(code), "Weather")


def get_open_meteo_weather(latitude, longitude, units):
    """Fetch weather from Open-Meteo as a fallback provider."""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None

    temp_unit = "&temperature_unit=fahrenheit" if units == "f" else ""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat:.4f}&longitude={lon:.4f}"
        f"&current=temperature_2m,weather_code&timezone=auto{temp_unit}"
    )
    try:
        if HAS_REQUESTS:
            r = requests.get(url, timeout=WEATHER_TIMEOUT, headers={"User-Agent": "SuperMouse/1.0"})
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            data = r.json()
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "SuperMouse/1.0"})
            with urllib.request.urlopen(req, timeout=WEATHER_TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8", errors="ignore"))

        current = data.get("current") or {}
        temp_value = current.get("temperature_2m")
        code = current.get("weather_code", 0)
        if temp_value is None:
            return None
        unit = "F" if units == "f" else "C"
        temp = f"{round(float(temp_value))}{unit}"
        cond = weather_code_text(code)[:14]
        print(f"[weather] open-meteo: {temp} {cond}")
        return temp, cond
    except Exception as e:
        print(f"[weather] open-meteo failed: {e}")
        return None


def get_weather():
    """Fetch weather from wttr.in (no API key needed)."""
    weather_cfg = CONFIG.get("weather", {})
    location = str(weather_cfg.get("location", "")).strip()
    units = str(weather_cfg.get("units", "m")).strip().lower()[:1] or "m"
    latitude = weather_cfg.get("latitude")
    longitude = weather_cfg.get("longitude")

    def fetch_wttr(place_name):
        place = urllib.parse.quote(place_name) if place_name else ""
        url = f"https://wttr.in/{place}?format=%t+%C&{units}"
        if HAS_REQUESTS:
            r = requests.get(url, timeout=WEATHER_TIMEOUT, headers={"User-Agent": "SuperMouse/1.0"})
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            raw = r.text
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "SuperMouse/1.0"})
            with urllib.request.urlopen(req, timeout=WEATHER_TIMEOUT) as response:
                raw = response.read().decode("utf-8", errors="ignore")

        return raw.strip().replace("+", "")

    fallback_locations = []
    if location:
        fallback_locations.append(location)
    else:
        fallback_locations.append("")
        fallback_locations.append("Bengaluru")

    for place_name in fallback_locations:
        try:
            raw = fetch_wttr(place_name)
        except Exception as e:
            print(f"[weather] fetch failed for {place_name or 'auto'}: {e}")
            continue

        if not raw or "unknown location" in raw.lower():
            print(f"[weather] no data for {place_name or 'auto'}")
            continue

        parts = raw.split(" ", 1)
        temp = parts[0].replace("\xb0C", "C").replace("\xb0F", "F").strip()
        cond = (parts[1] if len(parts) > 1 else "Clear")[:14].strip()
        print(f"[weather] {place_name or 'auto'}: {temp} {cond}")
        return temp, cond

    meteo = get_open_meteo_weather(latitude, longitude, units)
    if meteo:
        return meteo

    return "N/A", "Unavail"


def get_weather_for_place(place_name):
    """Fetch weather for a spoken place name."""
    place_name = str(place_name or "").strip()
    if not place_name:
        return None

    units = str(CONFIG.get("weather", {}).get("units", "m")).strip().lower()[:1] or "m"
    place = urllib.parse.quote(place_name)
    url = f"https://wttr.in/{place}?format=%t+%C&{units}"
    try:
        if HAS_REQUESTS:
            r = requests.get(url, timeout=WEATHER_TIMEOUT, headers={"User-Agent": "SuperMouse/1.0"})
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            raw = r.text
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "SuperMouse/1.0"})
            with urllib.request.urlopen(req, timeout=WEATHER_TIMEOUT) as response:
                raw = response.read().decode("utf-8", errors="ignore")

        raw = raw.strip().replace("+", "")
        if not raw or "unknown location" in raw.lower():
            return None
        parts = raw.split(" ", 1)
        temp = parts[0].replace("\xb0C", "C").replace("\xb0F", "F").strip()
        cond = (parts[1] if len(parts) > 1 else "Clear")[:14].strip()
        return temp, cond
    except Exception as e:
        print(f"[weather] voice fetch failed for {place_name}: {e}")
        return None


def send_line(ser, line):
    """Send one newline-terminated command to the board."""
    with SERIAL_LOCK:
        ser.write(f"{line}\n".encode())
        ser.flush()
    print(f"[tx] {line}")
    time.sleep(0.03)


def send_voice_status(ser, line1, line2=""):
    send_line(ser, f"VOICE {field(line1, 16)}|{field(line2, 18)}")


def send_assistant_status(ser, line1, line2=""):
    send_line(ser, f"VOICE {field('Assistant', 16)}|{field(line1 + (' ' + line2 if line2 else ''), 18)}")


def handle_weather_voice_command(ser, spoken):
    weather_query = re.match(
        r"^(?:(?:what is|what's|tell me|show me|check|get)\s+(?:the\s+)?weather|weather)\b",
        spoken,
    )
    if not weather_query:
        return False

    cleaned = re.sub(r"^(?:what is|what's|tell me|show me|check|get)\s+(?:the\s+)?weather\s*", "", spoken).strip()
    cleaned = re.sub(r"^(?:weather)\s*", "", cleaned).strip()
    cleaned = re.sub(r"\b(right now|now|today|currently|please)\b", "", cleaned).strip()

    # Avoid stealing normal dictation such as
    # "hello ... and the weather is hot ..." or
    # "what's the weather and the time/date is ...".
    if cleaned:
        mixed_dictation_words = (
            "time", "date", "also", "think", "rain", "hot", "cold", "cloudy",
            "partly", "degree", "degrees", "today evening", "tomorrow",
        )
        asks_for_location = re.match(r"^(?:in|at|for)\s+", cleaned)
        if cleaned.startswith(("and ", "also ")) or any(word in cleaned for word in mixed_dictation_words):
            return False
        if not asks_for_location and len(cleaned.split()) > 3:
            return False

    send_voice_status(ser, "Weather", "Checking")

    locations = []
    if cleaned:
        pieces = re.split(r"\s+(?:and\s+)?in\s+", cleaned)
        for piece in pieces:
            piece = re.sub(r"^(?:in|at|for)\s+", "", piece).strip(" ,.;")
            piece = re.sub(r"\b(right now|now|today|currently|please)\b", "", piece).strip(" ,.;")
            if piece and piece not in ("the", "weather"):
                locations.append(piece)

    if not locations:
        temp, cond = get_weather()
        send_line(ser, f"WEATHER {temp} {cond}")
        send_voice_status(ser, "Weather", f"{temp} {cond}")
        return True

    results = []
    for loc in locations[:3]:
        result = get_weather_for_place(loc)
        if result:
            temp, cond = result
            results.append((loc, temp, cond))
            print(f"[weather] voice {loc}: {temp} {cond}")
        else:
            results.append((loc, "N/A", "No data"))

    if results:
        first_loc, first_temp, first_cond = results[0]
        send_line(ser, f"WEATHER {first_temp} {first_cond}")
        summary = "; ".join(f"{loc.title()} {temp} {cond}" for loc, temp, cond in results)
        send_voice_status(ser, "Weather", summary[:18])
        print(f"[weather] voice summary: {summary}")
    return True


def normalize_keyboard_word(word):
    return re.sub(r"[^a-z]", "", (word or "").lower())[:17]


def save_config():
    try:
        CONFIG_PATH.write_text(json.dumps(CONFIG, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[config] failed to save {CONFIG_PATH.name}: {e}")


def learn_keyboard_word(word, ser=None):
    clean = normalize_keyboard_word(word)
    if len(clean) < 2:
        return
    KEYBOARD_WORDS[clean] = int(KEYBOARD_WORDS.get(clean, 0)) + 1
    CONFIG["keyboard_words"] = KEYBOARD_WORDS
    save_config()
    if ser:
        send_line(ser, f"KWORD {clean}")
    print(f"[keyboard] learned: {clean} ({KEYBOARD_WORDS[clean]})")


def learn_keyboard_pair(previous, next_word, ser=None):
    prev = normalize_keyboard_word(previous)
    nxt = normalize_keyboard_word(next_word)
    if len(prev) < 2 or len(nxt) < 2 or prev == nxt:
        return
    bucket = KEYBOARD_PAIRS.setdefault(prev, {})
    bucket[nxt] = int(bucket.get(nxt, 0)) + 1
    CONFIG["keyboard_pairs"] = KEYBOARD_PAIRS
    save_config()
    if ser:
        send_line(ser, f"KPAIR {prev}|{nxt}")
    print(f"[keyboard] learned pair: {prev} -> {nxt} ({bucket[nxt]})")


def keyboard_words_from_text(text):
    return [normalize_keyboard_word(word) for word in re.findall(r"[A-Za-z]{2,}", text or "")
            if len(normalize_keyboard_word(word)) >= 2]


def learn_keyboard_sentence(text, ser=None):
    words = keyboard_words_from_text(text)
    for word in words:
        learn_keyboard_word(word, ser)
    for previous, next_word in zip(words, words[1:]):
        learn_keyboard_pair(previous, next_word, ser)


def send_keyboard_words(ser):
    words = sorted(KEYBOARD_WORDS.items(), key=lambda item: (-int(item[1]), item[0]))[:48]
    for word, _count in words:
        clean = normalize_keyboard_word(word)
        if clean:
            send_line(ser, f"KWORD {clean}")
    pairs = []
    for previous, next_words in KEYBOARD_PAIRS.items():
        for next_word, count in (next_words or {}).items():
            pairs.append((normalize_keyboard_word(previous), normalize_keyboard_word(next_word), int(count)))
    pairs.sort(key=lambda item: (-item[2], item[0], item[1]))
    for previous, next_word, _count in pairs[:48]:
        if previous and next_word:
            send_line(ser, f"KPAIR {previous}|{next_word}")


def paste_voice_text_and_learn(ser, text):
    paste_text(text + " ")
    learn_keyboard_sentence(text, ser)


def assistant_store():
    store = CONFIG.setdefault("assistant", {})
    store.setdefault("notes", [])
    store.setdefault("reminders", [])
    store.setdefault("alarms", [])
    store.setdefault("timers", [])
    return store


def save_assistant_note(note):
    now = datetime.datetime.now()
    if not NOTES_PATH.exists():
        NOTES_PATH.write_text("# Super Mouse Notes\n\n", encoding="utf-8")
    with NOTES_PATH.open("a", encoding="utf-8") as f:
        f.write(f"- {now.strftime('%Y-%m-%d %H:%M')} - {note}\n")


def open_assistant_notes():
    if not NOTES_PATH.exists():
        NOTES_PATH.write_text("# Super Mouse Notes\n\n", encoding="utf-8")
    try:
        os.startfile(str(NOTES_PATH))
        return True
    except Exception as e:
        print(f"[assistant] open notes failed: {e}")
        return False


def extract_assistant_note_text(spoken):
    text = re.sub(r"\bplay store\b", "please store", spoken)
    patterns = [
        r"^(?:take|make|create|add|save|store)\s+(?:a\s+)?note(?:\s+(?:on|about|as|that|saying))?\s+(.+)$",
        r"^(?:note|notes)\s+(.+)$",
        r"^(?:please\s+)?(?:save|store|add|write|keep|put)\s+(.+?)\s+(?:in|to|into|on)\s+(?:the\s+)?notes?$",
        r"^(?:in|to|into|on)\s+(?:the\s+)?notes?\s+(?:please\s+)?(?:save|store|add|write|keep|put)\s+(.+)$",
        r"^(.+?)\s+(?:please\s+)?(?:save|store|add|write|keep|put)\s+(?:it\s+)?(?:in|to|into|on)\s+(?:the\s+)?notes?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            note = match.group(1).strip(" .,:;-")
            note = re.sub(r"^(?:that|as|is)\s+", "", note).strip(" .,:;-")
            if note:
                return note
    return None


def notes_for_date(day):
    notes = []
    prefix = day.isoformat()
    for note in assistant_store().get("notes", []):
        stamp = str(note.get("time", ""))
        if stamp.startswith(prefix):
            text = str(note.get("text", "")).strip()
            if text:
                notes.append(text)
    return notes


def build_notes_summary(day=None):
    day = day or datetime.date.today()
    notes = notes_for_date(day)
    title = f"Super Mouse notes summary - {day.strftime('%Y-%m-%d')}"
    if not notes:
        return title, "No notes were saved today."

    unique_notes = []
    seen = set()
    for note in notes:
        key = note.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_notes.append(note)

    lines = [
        title,
        "",
        "Today's notes:",
    ]
    for idx, note in enumerate(unique_notes, 1):
        lines.append(f"{idx}. {note}")
    return title, "\n".join(lines)


def email_setting(key):
    cfg = CONFIG.get("email_summary", {})
    env_key = "SUPERMOUSE_" + key.upper()
    return os.environ.get(env_key) or cfg.get(key, "")


def send_notes_summary_email(ser=None, manual=False):
    cfg = CONFIG.setdefault("email_summary", dict(DEFAULT_CONFIG["email_summary"]))
    smtp_host = email_setting("smtp_host")
    smtp_port = int(email_setting("smtp_port") or 587)
    smtp_user = email_setting("smtp_user")
    smtp_password = email_setting("smtp_password")
    mail_from = email_setting("from") or smtp_user
    mail_to = email_setting("to")

    if not (smtp_host and smtp_user and smtp_password and mail_from and mail_to):
        if ser:
            send_assistant_status(ser, "Email setup", "needed")
        print("[email] missing settings. Configure email_summary in supermouse_config.json or SUPERMOUSE_* env vars.")
        return False

    subject, body = build_notes_summary()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)
    except Exception as e:
        if ser:
            send_assistant_status(ser, "Email failed")
        print(f"[email] notes summary failed: {e}")
        return False

    cfg["last_sent_date"] = datetime.date.today().isoformat()
    save_config()
    if ser:
        send_assistant_status(ser, "Notes emailed")
    print(f"[email] notes summary sent to {mail_to} ({'manual' if manual else 'scheduled'})")
    return True


def check_notes_summary_schedule(ser):
    cfg = CONFIG.get("email_summary", {})
    if not cfg.get("enabled"):
        return

    today = datetime.date.today().isoformat()
    if cfg.get("last_sent_date") == today:
        return

    daily_time = str(cfg.get("daily_time") or "18:00")
    match = re.match(r"^(\d{1,2}):(\d{2})$", daily_time)
    if not match:
        return

    hour = int(match.group(1))
    minute = int(match.group(2))
    now = datetime.datetime.now()
    if now.hour > hour or (now.hour == hour and now.minute >= minute):
        send_notes_summary_email(ser)


def parse_assistant_time(text):
    spoken = normalize_voice_command(text)
    spoken = spoken.replace("p.m.", "pm").replace("a.m.", "am").replace("p m", "pm").replace("a m", "am")
    match = re.search(r"\b(?:at|for)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", spoken)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = match.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None

    due = datetime.datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= datetime.datetime.now():
        due += datetime.timedelta(days=1)
    return due


def assistant_alarm_same_minute(alarm, due):
    try:
        alarm_due = datetime.datetime.fromisoformat(alarm.get("due", ""))
    except Exception:
        return False
    return alarm_due.hour == due.hour and alarm_due.minute == due.minute


def assistant_set_alarm(store, due, label="Alarm"):
    alarm = {
        "id": str(uuid.uuid4())[:8],
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
        "due": due.isoformat(timespec="seconds"),
        "text": label,
        "done": False,
    }
    store["alarms"].append(alarm)
    return alarm


def parse_assistant_duration(text):
    spoken = normalize_voice_command(text)
    spoken = spoken.replace("an hour", "1 hour").replace("a hour", "1 hour")
    spoken = spoken.replace("a minute", "1 minute").replace("a second", "1 second")
    total_seconds = 0

    for match in re.finditer(
        r"\b(\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
        r"forty|fifty|sixty)\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b",
        spoken,
    ):
        value = parse_spoken_number(match.group(1))
        if value is None:
            continue
        unit = match.group(2)
        if unit.startswith(("hour", "hr")):
            total_seconds += int(value * 3600)
        elif unit.startswith(("minute", "min")):
            total_seconds += int(value * 60)
        else:
            total_seconds += int(value)

    bare_minutes = re.search(r"\b(?:timer|set timer|start timer)\s+(?:for\s+)?(\d{1,3})\b", spoken)
    if total_seconds == 0 and bare_minutes:
        total_seconds = int(bare_minutes.group(1)) * 60

    return total_seconds if total_seconds > 0 else None


def assistant_set_timer(store, seconds, label="Timer"):
    now = datetime.datetime.now()
    due = now + datetime.timedelta(seconds=seconds)
    timer = {
        "id": str(uuid.uuid4())[:8],
        "time": now.isoformat(timespec="seconds"),
        "due": due.isoformat(timespec="seconds"),
        "seconds": int(seconds),
        "text": label,
        "done": False,
    }
    store["timers"].append(timer)
    return timer


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hr")
    if minutes:
        parts.append(f"{minutes} min")
    if secs or not parts:
        parts.append(f"{secs} sec")
    return " ".join(parts)


def notify_alarm_set(due):
    show_windows_alert("Super Mouse Alarm Set", f"Alarm set for {due.strftime('%I:%M %p')}")


def notify_timer_set(seconds, due):
    show_windows_alert(
        "Super Mouse Timer Set",
        f"Timer set for {format_duration(seconds)}.\nEnds at {due.strftime('%I:%M:%S %p')}",
    )


def assistant_remove_alarm(store, due=None):
    alarms = store.get("alarms", [])
    remaining = []
    removed = []
    for alarm in alarms:
        if alarm.get("done"):
            remaining.append(alarm)
            continue
        if due is None or assistant_alarm_same_minute(alarm, due):
            removed.append(alarm)
        else:
            remaining.append(alarm)
    store["alarms"] = remaining
    return removed


def handle_assistant_alarm_command(ser, store, spoken):
    actions = []
    for match in re.finditer(r"\b(?:remove|delete|cancel|clear)\s+(?:the\s+)?alarm(?:\s+(?:at|for)\s+[^,;]+?)?(?=\s+and\s+|\s+then\s+|$)", spoken):
        actions.append((match.start(), "remove", match.group(0)))
    for match in re.finditer(r"\b(?:set\s+)?alarm\s+(?:at|for)?\s*[^,;]+?(?=\s+and\s+|\s+then\s+|$)", spoken):
        text = match.group(0)
        before = spoken[max(0, match.start() - 16):match.start()]
        if re.search(r"\b(?:remove|delete|cancel|clear)\s+(?:the\s+)?$", before):
            continue
        actions.append((match.start(), "set", text))

    if not actions:
        return False

    actions.sort(key=lambda item: item[0])
    removed_total = 0
    set_times = []
    for _pos, kind, phrase in actions:
        due = parse_assistant_time(phrase)
        if kind == "remove":
            removed = assistant_remove_alarm(store, due)
            removed_total += len(removed)
        else:
            if due:
                assistant_set_alarm(store, due)
                notify_alarm_set(due)
                set_times.append(due.strftime("%H:%M"))

    save_config()
    if removed_total and set_times:
        send_assistant_status(ser, f"Removed {removed_total}", f"set {set_times[-1]}")
    elif removed_total:
        send_assistant_status(ser, f"Removed {removed_total}", "alarm")
    elif set_times:
        send_assistant_status(ser, "Alarm set", set_times[-1])
    else:
        send_assistant_status(ser, "Alarm time?")
    print(f"[assistant] alarm actions: removed={removed_total}, set={set_times}")
    return True


def handle_assistant_command(ser, text):
    spoken = normalize_voice_command(text)
    if not spoken:
        send_assistant_status(ser, "Didn't catch")
        return

    store = assistant_store()
    if handle_assistant_alarm_command(ser, store, spoken):
        return

    if re.match(r"^(?:cancel|clear|remove|delete)\s+(?:the\s+)?timer\b", spoken):
        active = [t for t in store.get("timers", []) if not t.get("done")]
        for timer in active:
            timer["done"] = True
        save_config()
        send_assistant_status(ser, f"Canceled {len(active)}", "timer")
        show_windows_alert("Super Mouse Timer", f"Canceled {len(active)} active timer(s).")
        print(f"[assistant] timers canceled: {len(active)}")
        return

    if re.match(r"^(?:set|start)?\s*(?:a\s+)?timer\b", spoken):
        seconds = parse_assistant_duration(spoken)
        if not seconds:
            send_assistant_status(ser, "Timer time?")
            return
        timer = assistant_set_timer(store, seconds)
        save_config()
        due = datetime.datetime.fromisoformat(timer["due"])
        send_assistant_status(ser, "Timer set", format_duration(seconds))
        notify_timer_set(seconds, due)
        print(f"[assistant] timer set: {format_duration(seconds)} due {timer['due']}")
        return

    note_text = extract_assistant_note_text(spoken)
    if note_text:
        note = prepare_voice_typing_text(note_text)
        store["notes"].append({"time": datetime.datetime.now().isoformat(timespec="seconds"), "text": note})
        save_assistant_note(note)
        save_config()
        send_assistant_status(ser, "Note saved")
        print(f"[assistant] note saved: {note}")
        return

    if re.match(
        r"^(?:type|write|text|dictate|insert|open new tab|new tab|close tab|close window|"
        r"open chrome|open word|open excel|open powerpoint|open vs code|copy|paste|cut|"
        r"print|screenshot|select all|volume|brightness|mute)\b",
        spoken,
    ):
        send_assistant_status(ser, "Use Mic", "for PC control")
        print(f"[assistant] redirected PC control to Mic button: {spoken}")
        return

    reminder_match = re.match(r"^(?:remind me to|reminder to|remind)\s+(.+)$", spoken)
    if reminder_match:
        raw = reminder_match.group(1).strip()
        due = parse_assistant_time(raw)
        task = re.sub(r"\b(?:at|for)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", "", raw).strip(" ,.;")
        task = task or raw
        store["reminders"].append({
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "due": due.isoformat(timespec="seconds") if due else "",
            "text": prepare_voice_typing_text(task),
            "done": False,
        })
        save_config()
        send_assistant_status(ser, "Reminder saved", due.strftime("%H:%M") if due else "")
        print(f"[assistant] reminder saved: {task} {due or ''}")
        return

    alarm_match = re.match(r"^(?:set alarm|alarm)\s+(?:at|for)?\s*(.+)$", spoken)
    if alarm_match:
        due = parse_assistant_time("at " + alarm_match.group(1))
        if not due:
            send_assistant_status(ser, "Alarm time?")
            return
        store["alarms"].append({
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "due": due.isoformat(timespec="seconds"),
            "text": "Alarm",
            "done": False,
        })
        save_config()
        send_assistant_status(ser, "Alarm set", due.strftime("%H:%M"))
        notify_alarm_set(due)
        print(f"[assistant] alarm set: {due}")
        return

    if spoken in ("note", "notes", "show note", "show notes", "list note", "list notes", "open note", "open notes", "my notes"):
        notes = store.get("notes", [])
        if notes:
            latest = notes[-1]["text"]
            opened = open_assistant_notes()
            send_assistant_status(ser, "Notes opened" if opened else "Latest note", latest[:12])
            print(f"[assistant] latest note: {latest}")
        else:
            opened = open_assistant_notes()
            send_assistant_status(ser, "Notes opened" if opened else "No notes")
        return

    if spoken in (
        "send notes summary",
        "email notes summary",
        "mail notes summary",
        "send today notes",
        "send today's notes",
        "email today notes",
        "email today's notes",
        "send daily notes",
    ):
        send_notes_summary_email(ser, manual=True)
        return

    query = polish_assistant_question(spoken)
    try:
        open_chrome_target(query)
        send_assistant_status(ser, "Searching", query[:10])
        print(f"[assistant] searching: {query}")
    except Exception as e:
        print(f"[assistant] search failed: {e}")
        send_assistant_status(ser, "Ask failed")


def show_windows_alert(title, message):
    try:
        ctypes.windll.user32.MessageBeep(0xFFFFFFFF)
        threading.Thread(
            target=lambda: ctypes.windll.user32.MessageBoxW(None, message, title, 0x40),
            daemon=True,
        ).start()
    except Exception as e:
        print(f"[assistant] alert failed: {e}")


def check_assistant_alerts(ser):
    store = assistant_store()
    now = datetime.datetime.now()
    changed = False

    for item in store.get("alarms", []):
        if item.get("done"):
            continue
        due_text = item.get("due", "")
        if not due_text:
            continue
        try:
            due = datetime.datetime.fromisoformat(due_text)
        except ValueError:
            continue
        if due <= now:
            item["done"] = True
            changed = True
            send_assistant_status(ser, "Alarm", due.strftime("%H:%M"))
            show_windows_alert("Super Mouse Alarm", item.get("text") or "Alarm")
            print(f"[assistant] alarm due: {due_text}")

    for item in store.get("reminders", []):
        if item.get("done"):
            continue
        due_text = item.get("due", "")
        if not due_text:
            continue
        try:
            due = datetime.datetime.fromisoformat(due_text)
        except ValueError:
            continue
        if due <= now:
            item["done"] = True
            changed = True
            text = item.get("text") or "Reminder"
            send_assistant_status(ser, "Reminder", text[:10])
            show_windows_alert("Super Mouse Reminder", text)
            print(f"[assistant] reminder due: {text}")

    for item in store.get("timers", []):
        if item.get("done"):
            continue
        due_text = item.get("due", "")
        if not due_text:
            continue
        try:
            due = datetime.datetime.fromisoformat(due_text)
        except ValueError:
            continue
        if due <= now:
            item["done"] = True
            changed = True
            text = item.get("text") or "Timer"
            seconds = int(item.get("seconds") or 0)
            send_assistant_status(ser, "Timer done", format_duration(seconds))
            show_windows_alert("Super Mouse Timer Done", text)
            print(f"[assistant] timer due: {text} {due_text}")

    if changed:
        save_config()


def voice_command_payload(text, spoken, prefixes):
    """Return the original recognized text after a spoken command prefix."""
    for prefix in prefixes:
        if spoken == prefix:
            return ""
        if spoken.startswith(prefix + " "):
            return spoken[len(prefix):].strip()
    return None


def voice_command_payload_anywhere(spoken, prefixes):
    """Return text after a command word, even when filler words came first."""
    for prefix in prefixes:
        match = re.search(r"\b" + re.escape(prefix) + r"\b\s+(.+)$", spoken)
        if match:
            payload = match.group(1).strip()
            if payload:
                return payload
    return None


def is_type_arm_command(spoken):
    """Return true when the user only asked to start a one-shot typing capture."""
    clean = re.sub(r"^(please|can you|could you)\s+", "", spoken).strip()
    return clean in (
        "type",
        "write",
        "text",
        "dictate",
        "insert",
        "type this",
        "write this",
        "start type",
        "start typing once",
    )


def arm_voice_type_next(ser, line1="Type ready", line2="Speak text"):
    global voice_type_next_utterance, voice_text_context_until
    voice_type_next_utterance = True
    voice_text_context_until = time.time() + 45.0
    send_voice_status(ser, line1, line2)


def clear_voice_text_context():
    global voice_text_context_until
    voice_text_context_until = 0.0


def in_voice_text_context():
    return time.time() < voice_text_context_until


def should_type_in_recent_text_context(spoken):
    if not in_voice_text_context():
        return False
    command_patterns = (
        r"^(?:new tab|open new tab|close tab|close current tab|address bar|go to address bar)$",
        r"^(?:select all|copy|paste|cut|enter|press enter|new line|delete|backspace|undo|redo)$",
        r"^(?:gmail|google mail|open gmail|open google mail|gmail inbox|open inbox|inbox|sent mail|open sent|drafts|open drafts|compose email|compose mail|new email|write email|send email|send current email|send mail now|send email now|search gmail|search mail|next email|previous email|open email|reply email|forward email|archive email|delete email|refresh gmail|back to inbox)\b.*$",
        r"^(?:search|search for|google|look up)\s+.+$",
        r"^(?:open|launch|start|close)\s+.+$",
        r"^(?:set\s+)?(?:volume|brightness)\s*(?:to\s+)?\d{1,3}$",
        r"^(?:print|print it|print this|print image|print this image|print photo|print this photo|print picture|print this picture|print file|print document|print current file|print selected file|take print|take a print|print now|open camera|launch camera|start camera|take photo|take picture|take selfie|capture photo|volume up|volume down|increase volume|decrease volume|brightness up|brightness down|increase brightness|decrease brightness|mute|unmute|power saver on|power saver off|battery saver on|battery saver off|energy saver on|energy saver off|turn on power saver|turn off power saver|turn on energy saver|turn off energy saver)$",
    )
    return not any(re.match(pattern, spoken) for pattern in command_patterns)


def should_type_in_text_field(spoken, text_input_active):
    if not text_input_active:
        return False
    command_patterns = (
        r"^(?:new tab|open new tab|close tab|close current tab|address bar|go to address bar)$",
        r"^(?:select all|copy|paste|cut|enter|press enter|new line|delete|backspace|undo|redo)$",
        r"^(?:gmail|google mail|open gmail|open google mail|gmail inbox|open inbox|inbox|sent mail|open sent|drafts|open drafts|compose email|compose mail|new email|write email|send email|send current email|send mail now|send email now|search gmail|search mail|next email|previous email|open email|reply email|forward email|archive email|delete email|refresh gmail|back to inbox)\b.*$",
        r"^(?:search|search for|google|look up)\s+.+$",
        r"^(?:open|launch|start|close)\s+.+$",
        r"^(?:set\s+)?(?:volume|brightness)\s*(?:to\s+)?\d{1,3}$",
        r"^(?:print|print it|print this|print image|print this image|print photo|print this photo|print picture|print this picture|print file|print document|print current file|print selected file|take print|take a print|print now|open camera|launch camera|start camera|take photo|take picture|take selfie|capture photo|volume up|volume down|increase volume|decrease volume|brightness up|brightness down|increase brightness|decrease brightness|mute|unmute|power saver on|power saver off|battery saver on|battery saver off|energy saver on|energy saver off|turn on power saver|turn off power saver|turn on energy saver|turn off energy saver)$",
        r"^(?:what is|what's|tell me|show me|check|get)?\s*(?:the\s+)?weather(?:\s+(?:in|at|for)\s+[\w .-]+)?$",
    )
    if any(re.match(pattern, spoken) for pattern in command_patterns):
        return False
    return True


def is_voice_phrase(spoken, *phrases):
    """Return true if a spoken phrase matches one of the variants."""
    clean = spoken.replace("please ", "").strip()
    if any(clean == phrase for phrase in phrases):
        return True
    if len(clean) < 4:
        return False
    best = difflib.get_close_matches(clean, phrases, n=1, cutoff=0.82)
    if best:
        print(f"[voice] corrected command: {clean} -> {best[0]}")
        return True
    return False


def open_web_target(target):
    """Open a site directly, or search the web for a phrase."""
    url = web_target_to_url(target)
    if not url:
        return None
    os.startfile(url)
    return url


def chrome_command():
    """Return the configured Chrome executable or a PATH fallback."""
    for app in APPS:
        name = str(app.get("name", "")).lower()
        path = str(app.get("path", "")).strip()
        if name in ("chrome", "google chrome") and path:
            return path
    common = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if os.path.exists(common):
        return common
    return "chrome"


def open_chrome_target(target):
    """Open a site/search in Chrome, not the Windows default browser."""
    url = web_target_to_url(target)
    if not url:
        return None
    subprocess.Popen([chrome_command(), "--new-tab", url])
    return url


GMAIL_BASE_URL = "https://mail.google.com/mail/u/0/"


def open_chrome_url(url):
    """Open an exact URL in Chrome without normalizing query parameters."""
    subprocess.Popen([chrome_command(), "--new-tab", url])
    return url


def open_gmail_fragment(fragment=""):
    """Open a Gmail section in Chrome."""
    return open_chrome_url(GMAIL_BASE_URL + fragment)


def navigate_gmail_fragment(fragment=""):
    """Navigate the current Chrome tab to a Gmail section."""
    url = GMAIL_BASE_URL + fragment
    press_hotkey(VK_CONTROL, VK_L)
    time.sleep(0.12)
    paste_text(url)
    time.sleep(0.08)
    tap_key(VK_RETURN)
    return url


def spoken_email_to_address(text):
    """Convert a spoken email address like 'john at gmail dot com'."""
    value = normalize_voice_command(text)
    value = value.replace(" at the rate ", " at ")
    value = value.replace(" underscore ", "_").replace(" dash ", "-")
    value = re.sub(r"\s+at\s+", "@", value)
    value = re.sub(r"\s+dot\s+", ".", value)
    value = re.sub(r"\s+", "", value)
    return value


def parse_gmail_compose_fields(spoken):
    """Extract to/subject/body fields from a Gmail compose command."""
    payload = re.sub(
        r"^(?:gmail\s+)?(?:compose|new|write|send)\s+(?:an?\s+)?(?:email|mail)\b",
        "",
        spoken,
        count=1,
    ).strip()
    if not payload:
        return "", "", ""

    markers = []
    for key, pattern in (
        ("to", r"\bto\b"),
        ("subject", r"\bsubject\b"),
        ("body", r"\b(?:body|message)\b"),
    ):
        match = re.search(pattern, payload)
        if match:
            markers.append((match.start(), match.end(), key))

    if not markers:
        return "", "", ""

    markers.sort()
    fields = {"to": "", "subject": "", "body": ""}
    for index, (_, end, key) in enumerate(markers):
        next_start = markers[index + 1][0] if index + 1 < len(markers) else len(payload)
        fields[key] = payload[end:next_start].strip(" ,.;")

    return fields["to"], fields["subject"], fields["body"]


def parse_natural_gmail_compose_fields(spoken):
    """Parse natural commands like 'compose an email by saying ... send it to ...'."""
    if not re.match(r"^(?:gmail\s+)?(?:compose|new|write|send)\s+(?:an?\s+)?(?:email|mail)\b", spoken):
        return None

    payload = re.sub(
        r"^(?:gmail\s+)?(?:compose|new|write|send)\s+(?:an?\s+)?(?:email|mail)\b",
        "",
        spoken,
        count=1,
    ).strip(" ,.;")
    if not payload:
        return "", "", ""

    subject = ""
    subject_match = re.search(r"\b(?:with\s+(?:the\s+)?subject|subject)\s+(.+)$", payload)
    if subject_match:
        subject = subject_match.group(1).strip(" ,.;")
        payload = payload[:subject_match.start()].strip(" ,.;")

    recipient = ""
    recipient_match = re.search(r"\b(?:send\s+(?:it\s+)?to|to)\s+(.+)$", payload)
    if recipient_match:
        recipient = recipient_match.group(1).strip(" ,.;")
        payload = payload[:recipient_match.start()].strip(" ,.;")

    body = payload
    body_match = re.search(r"\b(?:by\s+saying|saying|message|body)\s+(.+)$", body)
    if body_match:
        body = body_match.group(1).strip(" ,.;")

    return recipient, subject, body


def open_gmail_compose(to="", subject="", body=""):
    """Open Gmail compose with optional recipient, subject, and body."""
    params = {"view": "cm", "fs": "1", "tf": "1"}
    if to:
        params["to"] = spoken_email_to_address(to)
    if subject:
        params["su"] = prepare_voice_typing_text(subject).rstrip(".")
    if body:
        params["body"] = prepare_voice_typing_text(body)
    return open_chrome_url(GMAIL_BASE_URL + "?" + urllib.parse.urlencode(params))


def handle_gmail_command(ser, spoken):
    """Handle Gmail-specific voice commands in Chrome."""
    gmail_open = re.search(
        r"\b(?:open\s+)?(?:gmail|google mail)(?:\s+(?:in|on)\s+chrome|\s+chrome)?\b",
        spoken,
    )
    if gmail_open:
        try:
            open_gmail_fragment("#inbox")
            send_voice_status(ser, "Opening", "Gmail")
        except Exception as e:
            print(f"[voice] Gmail open failed: {e}")
            send_voice_status(ser, "Open failed", "Gmail")
        return True

    section_map = (
        (("gmail inbox", "open inbox", "go to inbox", "show inbox", "inbox"), "#inbox", "Inbox"),
        (("gmail sent", "open sent", "sent mail", "show sent mail", "go to sent"), "#sent", "Sent"),
        (("gmail drafts", "open drafts", "show drafts", "go to drafts"), "#drafts", "Drafts"),
        (("gmail starred", "open starred", "show starred"), "#starred", "Starred"),
        (("gmail important", "open important", "show important"), "#imp", "Important"),
        (("gmail spam", "open spam", "show spam"), "#spam", "Spam"),
        (("gmail trash", "open trash", "show trash", "bin mail"), "#trash", "Trash"),
    )
    for phrases, fragment, label in section_map:
        if spoken in phrases:
            navigate_gmail_fragment(fragment)
            send_voice_status(ser, "Gmail", label)
            return True

    search_match = re.search(r"^(?:gmail\s+)?(?:search\s+(?:gmail|mail)\s+(?:for\s+)?|search\s+in\s+gmail\s+(?:for\s+)?)(.+)$", spoken)
    if search_match:
        query = search_match.group(1).strip(" ,.;")
        if query:
            navigate_gmail_fragment("#search/" + urllib.parse.quote(query))
            send_voice_status(ser, "Gmail search", query)
        else:
            send_voice_status(ser, "Search what?", "Gmail")
        return True

    compose_command = re.match(r"^(?:gmail\s+)?(?:compose|new|write|send)\s+(?:an?\s+)?(?:email|mail)\b", spoken)
    if compose_command:
        natural_fields = parse_natural_gmail_compose_fields(spoken)
        to, subject, body = natural_fields if natural_fields is not None else parse_gmail_compose_fields(spoken)
        open_gmail_compose(to, subject, body)
        send_voice_status(ser, "Compose", to or "Gmail")
        if not body:
            arm_voice_type_next(ser, "Compose ready", "Speak message")
        elif "send it" in spoken or "send email" in spoken or "send mail" in spoken:
            send_voice_status(ser, "Review email", "Say send now")
        return True

    if spoken in ("send current email", "send mail now", "send email now", "send this email", "send now"):
        press_hotkey(VK_CONTROL, VK_RETURN)
        send_voice_status(ser, "Gmail", "Send")
        return True

    action_keys = {
        "next email": key_code_for_char("j"),
        "next mail": key_code_for_char("j"),
        "previous email": key_code_for_char("k"),
        "previous mail": key_code_for_char("k"),
        "open selected email": VK_RETURN,
        "open email": VK_RETURN,
        "reply email": VK_R,
        "reply to email": VK_R,
        "forward email": VK_F,
        "archive email": VK_E,
        "refresh gmail": VK_R,
    }
    if spoken in action_keys:
        key = action_keys[spoken]
        if spoken == "refresh gmail":
            press_hotkey(VK_CONTROL, VK_R)
        else:
            tap_key(key)
        send_voice_status(ser, "Gmail", spoken[:18])
        return True

    if spoken in ("delete email", "delete mail", "move email to trash", "move mail to trash"):
        tap_key(VK_DELETE)
        send_voice_status(ser, "Gmail", "Delete")
        return True

    if spoken in ("back to inbox", "return to inbox"):
        navigate_gmail_fragment("#inbox")
        send_voice_status(ser, "Gmail", "Inbox")
        return True

    return False


def web_target_to_url(target):
    """Convert a spoken site/search target to a URL."""
    query = str(target).strip()
    if not query:
        return None

    query = normalize_voice_command(query)
    compact = query.lower().replace(" ", "")
    if compact in ("gmail", "gmailchrome", "gmailgooglechrome", "mailgoogle", "googlemail"):
        return "https://mail.google.com/"
    if "." in compact and not compact.startswith(("http://", "https://")):
        url = "https://" + compact
    elif compact.startswith(("http://", "https://")):
        url = compact
    else:
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    return url


def navigate_current_tab(target):
    """Navigate the active browser tab to a site/search target."""
    url = web_target_to_url(target)
    if not url:
        return None
    press_hotkey(VK_CONTROL, VK_L)
    time.sleep(0.15)
    paste_text(url)
    time.sleep(0.08)
    tap_key(VK_RETURN)
    return url


NUMBER_WORDS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}


def parse_spoken_number(value):
    """Parse either numeric text or simple spoken numbers up to thousands."""
    raw = str(value).lower().replace("-", " ").strip()
    raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        pass

    total = 0
    current = 0
    found = False
    for token in raw.split():
        if token in ("and", "a"):
            continue
        if token in NUMBER_WORDS:
            current += NUMBER_WORDS[token]
            found = True
        elif token == "hundred":
            current = max(current, 1) * 100
            found = True
        elif token == "thousand":
            total += max(current, 1) * 1000
            current = 0
            found = True
        elif token == "point":
            # Speech decimals are uncommon here; leave them for numeric input.
            return None
        else:
            return None
    return float(total + current) if found else None


def parse_spoken_count(value, default=None):
    """Parse a spoken count and clamp it to a practical shortcut range."""
    parsed = parse_spoken_number(value)
    if parsed is None:
        return default
    return max(1, min(25, int(parsed)))


def count_before_phrase(spoken, phrase, default=None):
    """Parse the number immediately before a phrase like 'new tabs'."""
    pattern = r"((?:\d+|[a-z]+)(?:\s+(?:hundred|thousand|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety))*)\s+" + re.escape(phrase)
    match = re.search(pattern, spoken)
    if not match:
        return default
    return parse_spoken_count(match.group(1), default)


def calculate_voice_expression(spoken):
    """Return a formatted result for a simple spoken arithmetic expression."""
    expr = spoken.lower().strip()
    expr = re.sub(r"^(calculate|calculator|what is|what's|compute)\s+", "", expr)
    expr = re.sub(r"\s+", " ", expr).strip()

    symbol_match = re.match(r"^(.+?)\s*(\*|x|×|star|asterisk|/|÷|\+|-)\s*(.+?)$", expr)
    if symbol_match:
        left = parse_spoken_number(symbol_match.group(1))
        right = parse_spoken_number(symbol_match.group(3))
        op = symbol_match.group(2)
        if left is not None and right is not None:
            if op in ("*", "x", "×", "star", "asterisk"):
                result = left * right
            elif op in ("/", "÷"):
                if right == 0:
                    return "Cannot divide by zero"
                result = left / right
            elif op == "+":
                result = left + right
            else:
                result = left - right
            if result == int(result):
                return str(int(result))
            return f"{result:.4f}".rstrip("0").rstrip(".")

    direct_patterns = [
        (r"^(multiplication of|multiply)\s+(.+)\s+and\s+(.+)$", "*"),
        (r"^(division of|divide)\s+(.+)\s+by\s+(.+)$", "/"),
        (r"^(addition of|add)\s+(.+)\s+and\s+(.+)$", "+"),
        (r"^(subtraction of|subtract)\s+(.+)\s+from\s+(.+)$", "-rev"),
        (r"^(subtract)\s+(.+)\s+and\s+(.+)$", "-"),
    ]
    for pattern, op in direct_patterns:
        match = re.match(pattern, expr)
        if not match:
            continue
        left = parse_spoken_number(match.group(2))
        right = parse_spoken_number(match.group(3))
        if left is None or right is None:
            return None
        if op == "/" and right == 0:
            return "Cannot divide by zero"
        if op == "*":
            result = left * right
        elif op == "/":
            result = left / right
        elif op == "+":
            result = left + right
        elif op == "-rev":
            result = right - left
        else:
            result = left - right
        if result == int(result):
            return str(int(result))
        return f"{result:.4f}".rstrip("0").rstrip(".")

    expr = expr.replace("multiplied with", "multiplied by")
    expr = re.sub(r"\bmultiply\s+(?!by\b)", "multiply by ", expr)

    ops = [
        (r"\s+(multiplied by|multiply by|times|x|into)\s+", "*"),
        (r"\s+(divided by|divide by|over)\s+", "/"),
        (r"\s+(plus|add)\s+", "+"),
        (r"\s+(minus|subtract)\s+", "-"),
    ]

    for pattern, op in ops:
        parts = re.split(pattern, expr, maxsplit=1)
        if len(parts) >= 3:
            left = parse_spoken_number(parts[0])
            right = parse_spoken_number(parts[2])
            if left is None or right is None:
                return None
            if op == "/" and right == 0:
                return "Cannot divide by zero"
            result = {
                "*": left * right,
                "/": left / right,
                "+": left + right,
                "-": left - right,
            }[op]
            if result == int(result):
                return str(int(result))
            return f"{result:.4f}".rstrip("0").rstrip(".")
    return None


def format_calc_result(result):
    if isinstance(result, str):
        return result
    if result == int(result):
        return str(int(result))
    return f"{result:.4f}".rstrip("0").rstrip(".")


def parse_calc_result(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_symbol_chain(spoken):
    """Evaluate bare arithmetic such as '20 x 20 / 100' left to right."""
    expr = spoken.lower().strip()
    expr = re.sub(r"^(calculate|calculator|what is|what's|compute)\s+", "", expr)
    expr = expr.replace("×", "x").replace("÷", "/")
    expr = re.sub(r"\b(multiplied by|multiply by|times|into|star|asterisk)\b", " x ", expr)
    expr = re.sub(r"\b(divided by|divide by|over)\b", " / ", expr)
    expr = re.sub(r"\b(plus|add)\b", " + ", expr)
    expr = re.sub(r"\b(minus|subtract)\b", " - ", expr)
    expr = re.sub(r"\s*([x*/+\-])\s*", r" \1 ", expr)
    expr = re.sub(r"\s+", " ", expr).strip()

    tokens = expr.split()
    if len(tokens) < 3 or len(tokens) % 2 == 0:
        return None

    result = parse_spoken_number(tokens[0])
    if result is None:
        return None

    used_op = False
    for pos in range(1, len(tokens), 2):
        op = tokens[pos]
        right = parse_spoken_number(tokens[pos + 1])
        if right is None or op not in ("x", "*", "/", "+", "-"):
            return None
        used_op = True
        if op in ("x", "*"):
            result *= right
        elif op == "/":
            if right == 0:
                return "Cannot divide by zero"
            result /= right
        elif op == "+":
            result += right
        else:
            result -= right

    return format_calc_result(result) if used_op else None


def calculate_chained_voice_expression(spoken):
    """Handle calculator phrases with a follow-up like 'and add 10 to that'."""
    expr = spoken.lower().strip()
    symbol_result = calculate_symbol_chain(expr)
    if symbol_result is not None:
        return symbol_result

    segments = [s.strip(" ,.;") for s in re.split(r"\s+and\s+", expr) if s.strip(" ,.;")]
    if len(segments) <= 1:
        if not re.match(r"^(calculate|calculator|what is|what's|compute)\b", expr):
            return None
        return calculate_voice_expression(expr)

    result_text = calculate_symbol_chain(segments[0]) or calculate_voice_expression(segments[0])
    result = parse_calc_result(result_text)
    if result is None:
        return None

    for segment in segments[1:]:
        segment = re.sub(r"\b(to|with)\s+(that|result|answer)\b", r"to that", segment)
        add_match = re.match(r"^(?:add|plus)\s+(.+?)(?:\s+to that)?$", segment)
        add_to_that_match = re.match(r"^(.+?)\s+to that$", segment)
        sub_match = re.match(r"^(?:subtract|minus)\s+(.+?)(?:\s+from that)?$", segment)
        mul_match = re.match(r"^(?:multiply|times|x|into)\s+(.+?)(?:\s+(?:with|by)\s+that)?$", segment)
        div_match = re.match(r"^(?:divide|divided by|over)\s+(.+?)(?:\s+(?:from|by)\s+that)?$", segment)

        if add_match:
            value = parse_spoken_number(add_match.group(1))
            if value is None:
                return None
            result += value
        elif add_to_that_match:
            value = parse_spoken_number(add_to_that_match.group(1))
            if value is None:
                return None
            result += value
        elif sub_match:
            value = parse_spoken_number(sub_match.group(1))
            if value is None:
                return None
            result -= value
        elif mul_match:
            value = parse_spoken_number(mul_match.group(1))
            if value is None:
                return None
            result *= value
        elif div_match:
            value = parse_spoken_number(div_match.group(1))
            if value is None:
                return None
            if value == 0:
                return "Cannot divide by zero"
            result /= value
        else:
            return None

    return format_calc_result(result)


def calculate_from_last_result(spoken):
    """Apply a short follow-up operation to the previous calculator result."""
    global last_calc_result

    if last_calc_result is None:
        return None

    expr = spoken.lower().strip()
    expr = re.sub(r"\b(to|with)\s+(that|result|answer)\b", "", expr).strip()
    patterns = (
        (r"^(?:add|plus)\s+(.+)$", "+"),
        (r"^(?:subtract|minus)\s+(.+)$", "-"),
        (r"^(?:multiply by|multiply|times|x|into)\s+(.+)$", "*"),
        (r"^(?:divide by|divide|divided by|over)\s+(.+)$", "/"),
    )

    for pattern, op in patterns:
        match = re.match(pattern, expr)
        if not match:
            continue
        value = parse_spoken_number(match.group(1))
        if value is None:
            return None
        if op == "+":
            last_calc_result += value
        elif op == "-":
            last_calc_result -= value
        elif op == "*":
            last_calc_result *= value
        elif op == "/":
            if value == 0:
                return "Cannot divide by zero"
            last_calc_result /= value
        return format_calc_result(last_calc_result)

    return None


def looks_like_calculator_command(spoken):
    """Return true when a failed phrase should not fall back to dictation."""
    if re.match(r"^(calculate|calculator|what is|what's|compute)\b", spoken):
        return True
    if re.match(r"^(add|plus|subtract|minus|multiply|multiply by|times|x|into|divide|divide by|divided by|over)\b", spoken):
        return last_calc_result is not None
    return bool(re.search(r"\d+\s*(?:x|\*|/|\+|-)\s*\d+", spoken.lower()))


def handle_editing_command(ser, spoken):
    """Handle common typing/editing/navigation voice commands."""
    try:
        press_match = re.match(r"^(?:press|tap)\s+(.+)$", spoken)
        if press_match:
            key = key_name_to_vk(press_match.group(1))
            if key is not None:
                tap_key(key)
                send_voice_status(ser, "Key", press_match.group(1)[:18])
                return True

        hotkey_match = re.match(r"^(?:control|ctrl)\s+([a-z0-9])$", spoken)
        if hotkey_match:
            key = key_code_for_char(hotkey_match.group(1))
            if key is not None:
                press_hotkey(VK_CONTROL, key)
                send_voice_status(ser, "Ctrl key", hotkey_match.group(1).upper())
                return True

        alt_match = re.match(r"^alt\s+([a-z0-9]|f\s*\d{1,2}|tab)$", spoken)
        if alt_match:
            key = key_name_to_vk(alt_match.group(1))
            if key is not None:
                press_hotkey(VK_MENU, key)
                send_voice_status(ser, "Alt key", alt_match.group(1)[:18])
                return True

        find_match = re.match(r"^(?:find|search in app|find in app)\s+(.+)$", spoken)
        if find_match:
            press_hotkey(VK_CONTROL, VK_F)
            time.sleep(0.1)
            paste_text(find_match.group(1))
            send_voice_status(ser, "Find", find_match.group(1)[:18])
            return True

        if is_voice_phrase(spoken, "save as"):
            tap_key(VK_F12)
            send_voice_status(ser, "File", "Save as")
            return True
        if is_voice_phrase(spoken, "new file", "new document", "new window file"):
            press_hotkey(VK_CONTROL, VK_N)
            send_voice_status(ser, "File", "New")
            return True
        if is_voice_phrase(spoken, "open file", "open document"):
            press_hotkey(VK_CONTROL, VK_O)
            send_voice_status(ser, "File", "Open")
            return True
        if is_voice_phrase(spoken, "zoom in", "increase zoom", "zoom plus", "make bigger", "make it bigger"):
            press_hotkey(VK_CONTROL, VK_OEM_PLUS)
            send_voice_status(ser, "Zoom", "In")
            return True
        if is_voice_phrase(spoken, "zoom out", "decrease zoom", "zoom minus", "make smaller", "make it smaller"):
            press_hotkey(VK_CONTROL, VK_OEM_MINUS)
            send_voice_status(ser, "Zoom", "Out")
            return True
        if is_voice_phrase(spoken, "reset zoom", "zoom reset", "normal zoom", "default zoom"):
            press_hotkey(VK_CONTROL, VK_0)
            send_voice_status(ser, "Zoom", "Reset")
            return True
        if is_voice_phrase(spoken, "go to top", "top of page", "top"):
            press_hotkey(VK_CONTROL, VK_HOME)
            send_voice_status(ser, "Move", "Top")
            return True
        if is_voice_phrase(spoken, "go to bottom", "bottom of page", "bottom"):
            press_hotkey(VK_CONTROL, VK_END)
            send_voice_status(ser, "Move", "Bottom")
            return True
        if is_voice_phrase(spoken, "page up"):
            tap_key(VK_PRIOR)
            send_voice_status(ser, "Key", "Page up")
            return True
        if is_voice_phrase(spoken, "page down"):
            tap_key(VK_NEXT)
            send_voice_status(ser, "Key", "Page down")
            return True

        tell_me_match = re.match(r"^(?:app command|office command|do command|tell me)\s+(.+)$", spoken)
        if tell_me_match:
            press_hotkey(VK_MENU, VK_Q)
            time.sleep(0.15)
            paste_text(tell_me_match.group(1))
            time.sleep(0.08)
            tap_key(VK_RETURN)
            send_voice_status(ser, "App command", tell_me_match.group(1)[:18])
            return True

        if is_voice_phrase(spoken, "enter", "press enter", "new line"):
            tap_key(VK_RETURN)
            send_voice_status(ser, "Key", "Enter")
            return True
        if is_voice_phrase(spoken, "click", "left click", "mouse click", "press click"):
            click_mouse(0)
            send_voice_status(ser, "Mouse", "Click")
            return True
        if is_voice_phrase(spoken, "right click", "mouse right click", "press right click"):
            click_mouse(1)
            send_voice_status(ser, "Mouse", "Right click")
            return True
        if is_voice_phrase(spoken, "double click", "double tap"):
            click_mouse(0)
            time.sleep(0.05)
            click_mouse(0)
            send_voice_status(ser, "Mouse", "Double click")
            return True
        scroll_match = re.match(r"^(?:scroll|role|roll)\s+(up|down)(?:\s+(\d+))?$", spoken)
        if scroll_match:
            direction = scroll_match.group(1)
            amount = int(scroll_match.group(2) or "4")
            amount = max(1, min(12, amount))
            scroll_mouse(amount if direction == "up" else -amount)
            send_voice_status(ser, "Scroll", direction)
            return True
        if is_voice_phrase(spoken, "space", "press space"):
            tap_key(VK_SPACE)
            send_voice_status(ser, "Key", "Space")
            return True
        if is_voice_phrase(spoken, "backspace", "delete last", "delete last character"):
            tap_key(VK_BACK)
            send_voice_status(ser, "Key", "Backspace")
            return True
        if is_voice_phrase(spoken, "delete", "press delete"):
            tap_key(VK_DELETE)
            send_voice_status(ser, "Key", "Delete")
            return True
        if is_voice_phrase(spoken, "tab", "press tab"):
            tap_key(VK_TAB)
            send_voice_status(ser, "Key", "Tab")
            return True
        if is_voice_phrase(spoken, "escape", "press escape"):
            tap_key(VK_ESCAPE)
            send_voice_status(ser, "Key", "Escape")
            return True
        if is_voice_phrase(spoken, "select all"):
            press_hotkey(VK_CONTROL, VK_A)
            send_voice_status(ser, "Edit", "Select all")
            return True
        if is_voice_phrase(spoken, "copy", "copy this", "copy selected", "copy selection"):
            press_hotkey(VK_CONTROL, VK_C)
            send_voice_status(ser, "Edit", "Copy")
            return True
        if is_voice_phrase(spoken, "paste", "paste here", "paste this"):
            press_hotkey(VK_CONTROL, VK_V)
            send_voice_status(ser, "Edit", "Paste")
            return True
        if is_voice_phrase(spoken, "cut", "cut this", "cut selected", "cut selection"):
            press_hotkey(VK_CONTROL, VK_X)
            send_voice_status(ser, "Edit", "Cut")
            return True
        if is_voice_phrase(spoken, "undo"):
            press_hotkey(VK_CONTROL, VK_Z)
            send_voice_status(ser, "Edit", "Undo")
            return True
        if is_voice_phrase(spoken, "redo"):
            press_hotkey(VK_CONTROL, VK_Y)
            send_voice_status(ser, "Edit", "Redo")
            return True
        if is_voice_phrase(spoken, "save", "save file", "save this"):
            press_hotkey(VK_CONTROL, VK_S)
            send_voice_status(ser, "Edit", "Save")
            return True
        if is_voice_phrase(spoken, "print now", "print directly", "send to printer"):
            open_print_dialog(auto_confirm=True)
            send_voice_status(ser, "Print", "Sent")
            return True
        if is_voice_phrase(
            spoken,
            "print", "print it", "print this", "print file", "print document",
            "print image", "print this image", "print photo", "print this photo",
            "print picture", "print this picture",
            "print current file", "print selected file", "print opened file",
            "take print", "take a print", "open print dialog",
        ):
            open_print_dialog(auto_confirm=False)
            send_voice_status(ser, "Print", "Dialog")
            return True
        if is_voice_phrase(spoken, "screenshot", "take screenshot", "screen shot", "take screen shot"):
            press_hotkey(VK_LWIN, VK_SHIFT, VK_S)
            send_voice_status(ser, "Screenshot", "Snipping")
            return True
        if is_voice_phrase(spoken, "copy screenshot", "full screenshot", "print screen"):
            tap_key(VK_SNAPSHOT)
            send_voice_status(ser, "Screenshot", "Copied")
            return True
        arrows = {
            "move left": VK_LEFT, "move cursor left": VK_LEFT, "cursor left": VK_LEFT,
            "go left": VK_LEFT, "left arrow": VK_LEFT,
            "move right": VK_RIGHT, "move cursor right": VK_RIGHT, "cursor right": VK_RIGHT,
            "go right": VK_RIGHT, "right arrow": VK_RIGHT,
            "move up": VK_UP, "move cursor up": VK_UP, "cursor up": VK_UP,
            "go up": VK_UP, "up arrow": VK_UP,
            "move down": VK_DOWN, "move cursor down": VK_DOWN, "cursor down": VK_DOWN,
            "go down": VK_DOWN, "down arrow": VK_DOWN,
        }
        if spoken in arrows:
            tap_key(arrows[spoken])
            send_voice_status(ser, "Key", spoken)
            return True
    except Exception as e:
        print(f"[voice] editing command failed: {e}")
        send_voice_status(ser, "Key failed", "Try again")
        return True
    return False


def handle_window_command(ser, spoken):
    """Handle browser/window/system voice shortcuts."""
    try:
        close_match = re.match(r"^(?:close|quit|exit|stop)\s+(.+)$", spoken)
        if close_match:
            target = close_match.group(1).strip()
            if close_named_app(target):
                send_voice_status(ser, "Closed", target)
                return True
        tabs_match = re.match(r"^(?:open\s+)?(.+?)\s+new tabs?$", spoken)
        if tabs_match:
            count = parse_spoken_count(tabs_match.group(1), 1)
            for _ in range(count):
                press_hotkey(VK_CONTROL, VK_T)
                time.sleep(0.05)
            if count == 1:
                press_hotkey(VK_CONTROL, VK_L)
                time.sleep(0.08)
                arm_voice_type_next(ser, "New tab", "Speak text")
            else:
                send_voice_status(ser, "Browser", f"{count} new tabs")
            return True
        if is_voice_phrase(spoken, "new tab", "open new tab"):
            press_hotkey(VK_CONTROL, VK_T)
            time.sleep(0.15)
            press_hotkey(VK_CONTROL, VK_L)
            time.sleep(0.08)
            arm_voice_type_next(ser, "New tab", "Speak text")
            return True
        if is_voice_phrase(spoken, "close tab", "close current tab"):
            press_hotkey(VK_CONTROL, VK_W)
            send_voice_status(ser, "Browser", "Close tab")
            return True
        if is_voice_phrase(spoken, "new window", "open new window"):
            press_hotkey(VK_CONTROL, VK_N)
            send_voice_status(ser, "Browser", "New window")
            return True
        if is_voice_phrase(spoken, "address bar", "go to address bar"):
            press_hotkey(VK_CONTROL, VK_L)
            send_voice_status(ser, "Browser", "Address bar")
            return True
        if is_voice_phrase(spoken, "switch window", "next window", "change window"):
            press_hotkey(VK_MENU, VK_TAB)
            send_voice_status(ser, "Windows", "Switch")
            return True
        if is_voice_phrase(
            spoken,
            "close window", "close app", "close current app", "close active app",
            "close current window", "close active window", "close software",
            "close program", "close application",
        ):
            press_hotkey(VK_MENU, VK_F4)
            send_voice_status(ser, "Windows", "Close")
            return True
        if is_voice_phrase(spoken, "minimize window", "minimise window", "minimize app", "minimise app"):
            press_hotkey(VK_LWIN, VK_DOWN)
            send_voice_status(ser, "Windows", "Minimize")
            return True
        if is_voice_phrase(spoken, "maximize window", "maximise window", "maximize app", "maximise app"):
            press_hotkey(VK_LWIN, VK_UP)
            send_voice_status(ser, "Windows", "Maximize")
            return True
        if is_voice_phrase(
            spoken,
            "lock screen", "lock computer", "lock pc", "lock laptop",
            "lock my pc", "lock my laptop", "lock the pc", "lock the laptop",
        ):
            if ctypes.windll.user32.LockWorkStation():
                send_voice_status(ser, "Windows", "Locked")
            else:
                press_hotkey(VK_LWIN, VK_L)
                send_voice_status(ser, "Windows", "Lock sent")
            return True
        if is_voice_phrase(spoken, "show desktop"):
            press_hotkey(VK_LWIN, 0x44)
            send_voice_status(ser, "Windows", "Desktop")
            return True
        if is_voice_phrase(spoken, "open settings"):
            os.startfile("ms-settings:")
            send_voice_status(ser, "Opening", "Settings")
            return True
        if is_voice_phrase(spoken, "open calculator"):
            if focus_or_launch_calculator():
                send_voice_status(ser, "Opening", "Calculator")
            else:
                send_voice_status(ser, "Open failed", "Calculator")
            return True
        if is_voice_phrase(spoken, "open notepad"):
            subprocess.Popen(["notepad.exe"])
            send_voice_status(ser, "Opening", "Notepad")
            return True
    except Exception as e:
        print(f"[voice] window command failed: {e}")
        send_voice_status(ser, "Command failed", "Try again")
        return True
    return False


def close_named_app(target):
    """Close a named app using a normal Windows close request."""
    app = normalize_voice_command(target)
    if app in ("current", "current app", "active app", "active window", "window", "this app", "this window"):
        press_hotkey(VK_MENU, VK_F4)
        return True

    process_aliases = {
        "chrome": ["chrome.exe"],
        "google chrome": ["chrome.exe"],
        "browser": ["chrome.exe", "msedge.exe"],
        "edge": ["msedge.exe"],
        "microsoft edge": ["msedge.exe"],
        "excel": ["EXCEL.EXE"],
        "workbook": ["EXCEL.EXE"],
        "word": ["WINWORD.EXE"],
        "document": ["WINWORD.EXE"],
        "powerpoint": ["POWERPNT.EXE"],
        "power point": ["POWERPNT.EXE"],
        "presentation": ["POWERPNT.EXE"],
        "outlook": ["OUTLOOK.EXE"],
        "onenote": ["ONENOTE.EXE"],
        "one note": ["ONENOTE.EXE"],
        "notepad": ["notepad.exe"],
        "calculator": ["CalculatorApp.exe", "calc.exe"],
        "vs code": ["Code.exe"],
        "visual studio code": ["Code.exe"],
        "settings": ["SystemSettings.exe"],
        "camera": ["WindowsCamera.exe", "Camera.exe"],
        "windows camera": ["WindowsCamera.exe", "Camera.exe"],
    }
    for configured in APPS:
        name = normalize_voice_command(configured.get("name", ""))
        abbr = normalize_voice_command(configured.get("abbr", ""))
        path = str(configured.get("path", "")).strip()
        if app in (name, abbr) and path and path not in ("__touchpad__", "ms-settings:"):
            exe = os.path.basename(path)
            if "." not in exe:
                exe += ".exe"
            process_aliases.setdefault(app, []).append(exe)

    names = process_aliases.get(app)
    if not names:
        return False

    closed_any = False
    for name in names:
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/IM", name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                closed_any = True
            else:
                detail = (result.stderr or result.stdout or "").strip()
                if detail:
                    print(f"[voice] close {name}: {detail}")
        except Exception as e:
            print(f"[voice] close {name} failed: {e}")
    return closed_any


def handle_voice_app_open(ser, target):
    """Open configured app by voice target."""
    target = normalize_voice_command(target)
    if not target:
        return False
    if target in (
        "gmail", "gmail chrome", "gmail google chrome", "google mail",
        "mail google", "open gmail", "open gmail chrome", "open gmail in chrome",
    ):
        try:
            open_chrome_target("gmail")
            send_voice_status(ser, "Opening", "Gmail")
        except Exception as e:
            print(f"[voice] open Gmail failed: {e}")
            send_voice_status(ser, "Open failed", "Gmail")
        return True
    if "." in target and " " not in target:
        try:
            open_chrome_target(target)
            send_voice_status(ser, "Opening", target)
        except Exception as e:
            print(f"[voice] open site failed: {e}")
            send_voice_status(ser, "Open failed", target)
        return True
    for idx, app in enumerate(APPS):
        name = str(app.get("name", "")).lower()
        abbr = str(app.get("abbr", "")).lower()
        aliases = [name, abbr]
        if name == "powerpnt":
            aliases.extend(["powerpoint", "power point"])
        if name == "vs code":
            aliases.extend(["visual studio code", "code"])
        if target in aliases or any(target in alias for alias in aliases if alias):
            launch_app(idx)
            send_voice_status(ser, "Opening", app.get("name", "App"))
            return True
    return False


def open_office_app(app_name):
    """Open an Office app by common launcher command."""
    commands = {
        "word": "winword",
        "excel": "excel",
        "powerpoint": "powerpnt",
        "power point": "powerpnt",
        "powerpnt": "powerpnt",
        "outlook": "outlook",
        "onenote": "onenote",
        "one note": "onenote",
        "access": "msaccess",
        "publisher": "mspub",
    }
    cmd = commands.get(app_name)
    if not cmd:
        return False
    subprocess.Popen(["cmd", "/c", "start", "", cmd])
    return True


def open_excel_blank_workbook():
    """Open Excel directly into a blank workbook."""
    try:
        if not activate_excel_window():
            subprocess.Popen(["cmd", "/c", "start", "", "excel", "/x"])
            time.sleep(2.5)
            activate_excel_window()
        else:
            time.sleep(0.3)
        tap_key(VK_ESCAPE)
        time.sleep(0.15)
        press_hotkey(VK_CONTROL, VK_N)
        time.sleep(0.25)
        return True
    except Exception as e:
        print(f"[voice] excel blank failed: {e}")
        try:
            subprocess.Popen(["cmd", "/c", "start", "", "excel", "/x"])
            time.sleep(2.0)
            activate_excel_window()
            tap_key(VK_ESCAPE)
            time.sleep(0.15)
            press_hotkey(VK_CONTROL, VK_N)
            return True
        except Exception as inner:
            print(f"[voice] excel blank fallback failed: {inner}")
            return False


def office_app_from_phrase(spoken):
    """Find a Microsoft app mentioned in a phrase."""
    if re.search(r"\b(?:gmail|google mail)\b", spoken):
        return None

    def contains_alias(text, alias):
        return re.search(r"\b" + re.escape(alias) + r"\b", text) is not None

    office_aliases = [
        ("powerpoint", ("powerpoint", "power point", "presentation")),
        ("excel", ("excel", "spreadsheet", "workbook")),
        ("word", ("word", "document", "doc")),
        ("outlook", ("outlook", "email", "mail")),
        ("onenote", ("onenote", "one note", "notes")),
        ("access", ("access", "database")),
        ("publisher", ("publisher", "publication")),
    ]
    for app, aliases in office_aliases:
        if any(contains_alias(spoken, alias) for alias in aliases):
            return app
    return None


def spoken_cell_reference(text):
    """Convert spoken cell text like 'a one' or 'B12' into an Excel reference."""
    raw = normalize_voice_command(text)
    raw = re.sub(r"^(go to|goto|select|move to)\s+", "", raw).strip()
    raw = re.sub(r"^(cell|sell)\s+", "", raw).strip()
    compact = raw.replace(" ", "").upper()
    if re.match(r"^[A-Z]{1,3}\d{1,7}$", compact):
        return compact

    match = re.match(r"^([a-z]{1,3})\s+(.+)$", raw)
    if not match:
        return None
    number = parse_spoken_number(match.group(2))
    if number is None:
        return None
    return f"{match.group(1).upper()}{int(number)}"


def excel_go_to_reference(ref):
    """Use Excel's Go To box to focus a cell/range."""
    press_hotkey(VK_CONTROL, VK_G)
    time.sleep(0.12)
    paste_text(ref)
    time.sleep(0.08)
    tap_key(VK_RETURN)


def handle_excel_command(ser, spoken):
    """Handle Excel-specific voice commands."""
    try:
        excel_command = re.match(
            r"^(?:excel|spreadsheet|workbook|select|go to|goto|move|cursor|move cursor|"
            r"merge|unmerge|insert pivot|pivot|create pivot|format as table|create table|"
            r"insert table|filter|toggle filter|apply filter|autosum|auto sum|bold|italic|"
            r"underline|new sheet|insert sheet|new worksheet|next sheet|previous sheet|"
            r"previous worksheet|freeze panes|freeze pane|rename sheet|rename worksheet|"
            r"type formula|formula)\b",
            spoken,
        )
        if not excel_command:
            return False

        should_activate = any(token in spoken for token in (
            "cell", "row", "column", "cursor", "merge", "pivot", "table", "filter",
            "autosum", "auto sum", "bold", "italic", "underline", "sheet",
            "freeze", "formula",
        )) or re.match(r"^(?:move|go|select)\b", spoken)
        if should_activate and not activate_excel_window():
            open_office_app("excel")
            time.sleep(1.6)
            activate_excel_window()

        row_match = re.match(r"^(?:select|go to)\s+row\s+(.+)$", spoken)
        if row_match:
            row = parse_spoken_number(row_match.group(1))
            if row is not None:
                excel_go_to_reference(f"{int(row)}:{int(row)}")
                send_voice_status(ser, "Excel row", str(int(row)))
                return True

        col_match = re.match(r"^(?:select|go to)\s+column\s+([a-z])$", spoken)
        if col_match:
            col = col_match.group(1).upper()
            excel_go_to_reference(f"{col}:{col}")
            send_voice_status(ser, "Excel column", col)
            return True

        cell_match = re.search(r"\b(?:go to|goto|select|move to)\s+(?:cell\s+)?([a-z]{1,3}\s*\d+|[a-z]{1,3}\s+(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety).*)$", spoken)
        if cell_match:
            ref = spoken_cell_reference(cell_match.group(1))
            if ref:
                excel_go_to_reference(ref)
                send_voice_status(ser, "Excel cell", ref)
                return True

        move_match = re.match(r"^(?:move|go|cursor|move cursor)\s+(up|down|left|right)(?:\s+(.+))?$", spoken)
        if move_match:
            key = {"up": VK_UP, "down": VK_DOWN, "left": VK_LEFT, "right": VK_RIGHT}[move_match.group(1)]
            count = parse_spoken_count(move_match.group(2) or "one", 1)
            for _ in range(count):
                tap_key(key)
                time.sleep(0.02)
            send_voice_status(ser, "Excel move", move_match.group(1))
            return True

        if is_voice_phrase(spoken, "merge cells", "merge cell"):
            press_ribbon_sequence("H", "M", "M")
            send_voice_status(ser, "Excel", "Merge cells")
            return True
        if is_voice_phrase(spoken, "merge and center", "merge and centre"):
            press_ribbon_sequence("H", "M", "C")
            send_voice_status(ser, "Excel", "Merge center")
            return True
        if is_voice_phrase(spoken, "unmerge cells", "unmerge cell"):
            press_ribbon_sequence("H", "M", "U")
            send_voice_status(ser, "Excel", "Unmerge")
            return True
        if is_voice_phrase(spoken, "insert pivot table", "pivot table", "create pivot table"):
            press_ribbon_sequence("N", "V", "T")
            send_voice_status(ser, "Excel", "Pivot table")
            return True
        if is_voice_phrase(spoken, "format as table", "create table", "insert table"):
            press_hotkey(VK_CONTROL, VK_T)
            send_voice_status(ser, "Excel", "Table")
            return True
        if is_voice_phrase(spoken, "filter", "toggle filter", "apply filter"):
            press_hotkey(VK_CONTROL, VK_SHIFT, VK_L)
            send_voice_status(ser, "Excel", "Filter")
            return True
        if is_voice_phrase(spoken, "autosum", "auto sum"):
            press_hotkey(VK_MENU, VK_OEM_PLUS)
            send_voice_status(ser, "Excel", "AutoSum")
            return True
        if is_voice_phrase(spoken, "bold"):
            press_hotkey(VK_CONTROL, VK_B)
            send_voice_status(ser, "Excel", "Bold")
            return True
        if is_voice_phrase(spoken, "italic"):
            press_hotkey(VK_CONTROL, VK_I)
            send_voice_status(ser, "Excel", "Italic")
            return True
        if is_voice_phrase(spoken, "underline"):
            press_hotkey(VK_CONTROL, VK_U)
            send_voice_status(ser, "Excel", "Underline")
            return True
        if is_voice_phrase(spoken, "new sheet", "insert sheet", "new worksheet"):
            press_hotkey(VK_SHIFT, VK_F11)
            send_voice_status(ser, "Excel", "New sheet")
            return True
        if is_voice_phrase(spoken, "next sheet"):
            press_hotkey(VK_CONTROL, VK_NEXT)
            send_voice_status(ser, "Excel", "Next sheet")
            return True
        if is_voice_phrase(spoken, "previous sheet", "previous worksheet"):
            press_hotkey(VK_CONTROL, VK_PRIOR)
            send_voice_status(ser, "Excel", "Prev sheet")
            return True
        if is_voice_phrase(spoken, "freeze panes", "freeze pane"):
            press_ribbon_sequence("W", "F", "F")
            send_voice_status(ser, "Excel", "Freeze panes")
            return True
        if is_voice_phrase(spoken, "rename sheet", "rename worksheet"):
            press_ribbon_sequence("H", "O", "R")
            send_voice_status(ser, "Excel", "Rename sheet")
            return True

        formula_match = re.match(r"^(?:type formula|formula)\s+(.+)$", spoken)
        if formula_match:
            formula = formula_match.group(1).strip()
            if not formula.startswith("="):
                formula = "=" + formula
            paste_text(formula)
            send_voice_status(ser, "Excel formula", formula[:18])
            return True
    except Exception as e:
        print(f"[voice] excel command failed: {e}")
        send_voice_status(ser, "Excel failed", "Try again")
        return True

    return False


def handle_office_command(ser, spoken):
    """Handle common Microsoft Office voice commands."""
    app = office_app_from_phrase(spoken)
    has_office_intent = app or any(
        phrase in spoken for phrase in (
            "blank workbook", "blank document", "blank presentation",
            "new workbook", "new document", "new presentation", "new slide",
        )
    )
    if not has_office_intent:
        return False

    try:
        if app and spoken.startswith(("open ", "launch ", "start ")) and not any(
            phrase in spoken for phrase in ("blank", "new", "save", "print", "open file")
        ):
            if open_office_app(app):
                send_voice_status(ser, "Opening", app.title())
                return True

        if any(phrase in spoken for phrase in (
            "blank workbook", "new workbook", "open new blank workbook",
            "open blank workbook", "open a blank workbook",
            "open blank workbook in excel", "open new blank workbook in excel",
            "excel blank workbook", "excel new workbook",
            "blank excel", "new excel",
        )):
            if open_excel_blank_workbook():
                send_voice_status(ser, "Excel", "Blank workbook")
            else:
                send_voice_status(ser, "Excel failed", "Try again")
            return True

        if any(phrase in spoken for phrase in (
            "blank document", "new document", "open new blank document",
            "blank word", "new word",
        )):
            open_office_app("word")
            time.sleep(1.0)
            press_hotkey(VK_CONTROL, VK_N)
            send_voice_status(ser, "Word", "Blank document")
            return True

        if any(phrase in spoken for phrase in (
            "blank presentation", "new presentation", "open new blank presentation",
            "blank powerpoint", "new powerpoint",
        )):
            open_office_app("powerpoint")
            time.sleep(1.0)
            press_hotkey(VK_CONTROL, VK_N)
            send_voice_status(ser, "PowerPoint", "Blank file")
            return True

        if "new slide" in spoken or "add slide" in spoken:
            press_hotkey(VK_CONTROL, VK_M)
            send_voice_status(ser, "PowerPoint", "New slide")
            return True

        if "save as" in spoken:
            tap_key(0x7B)  # F12 in Office Save As
            send_voice_status(ser, "Office", "Save as")
            return True

        if "save" in spoken:
            press_hotkey(VK_CONTROL, VK_S)
            send_voice_status(ser, "Office", "Save")
            return True

        if "print" in spoken:
            press_hotkey(VK_CONTROL, VK_P)
            send_voice_status(ser, "Office", "Print")
            return True

        if "open file" in spoken or "open document" in spoken or "open workbook" in spoken:
            press_hotkey(VK_CONTROL, VK_O)
            send_voice_status(ser, "Office", "Open file")
            return True
    except Exception as e:
        print(f"[voice] office command failed: {e}")
        send_voice_status(ser, "Office failed", "Try again")
        return True

    return False


def handle_chrome_tabs_search_command(ser, spoken):
    """Handle compound browser commands like Chrome + many tabs + final search."""
    if "chrome" not in spoken or "new tab" not in spoken:
        return False

    count = count_before_phrase(spoken, "new tabs", 1)

    target = ""
    target_match = re.search(
        r"(?:last tab\s+)?(?:search for|search|go to|browse|google)\s+(.+)$",
        spoken,
    )
    if target_match:
        target = target_match.group(1).strip()

    try:
        handle_voice_app_open(ser, "chrome")
        time.sleep(1.2)
        for _ in range(count):
            press_hotkey(VK_CONTROL, VK_T)
            time.sleep(0.05)
        if target:
            navigate_current_tab(target)
            send_voice_status(ser, "Chrome tabs", f"{count} + search")
            print(f"[voice] opened Chrome, created {count} tabs, searched {target}")
        else:
            send_voice_status(ser, "Chrome tabs", str(count))
            print(f"[voice] opened Chrome and created {count} tabs")
    except Exception as e:
        print(f"[voice] compound browser command failed: {e}")
        send_voice_status(ser, "Command failed", "Chrome tabs")
    return True


def handle_long_intent_sequence(ser, spoken):
    """Extract and execute several command intents from one long sentence."""
    if spoken.startswith(("type ", "write ", "text ", "dictate ")):
        return False
    if calculate_voice_expression(spoken):
        return False

    actions = []

    app_aliases = {
        "chrome": ("chrome", "browser"),
        "word": ("word", "document"),
        "excel": ("excel", "workbook", "spreadsheet"),
        "powerpoint": ("powerpoint", "presentation"),
        "vs code": ("visual studio code", "code"),
        "notepad": ("notepad",),
        "calculator": ("calculator",),
        "settings": ("settings",),
    }
    for app_name, aliases in app_aliases.items():
        for alias in aliases:
            match = re.search(r"\b(?:open|launch|start)\s+" + re.escape(alias) + r"\b", spoken)
            if match:
                actions.append((match.start(), "open_app", app_name))
                break

    tab_match = re.search(
        r"\b((?:\d+|[a-z]+)(?:\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
        r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety))*)\s+new tabs?\b",
        spoken,
    )
    if tab_match:
        actions.append((tab_match.start(), "new_tabs", parse_spoken_count(tab_match.group(1), 1)))
    elif re.search(r"\bnew tab\b", spoken):
        match = re.search(r"\bnew tab\b", spoken)
        actions.append((match.start(), "new_tabs", 1))

    search_matches = list(re.finditer(
        r"\b(?:in\s+(?:the\s+)?last\s+tab\s+)?(?:search\s+for|search|google|look\s+up|go\s+to|browse)\s+(.+)$",
        spoken,
    ))
    if search_matches:
        match = search_matches[-1]
        target = match.group(1).strip(" ,.;")
        if target:
            actions.append((match.start(), "search", target))

    type_match = re.search(r"\b(?:type|write|text|dictate|insert)\s+(.+)$", spoken)
    if type_match and not search_matches:
        actions.append((type_match.start(), "type", type_match.group(1).strip()))

    calc_match = re.search(r"\b(?:calculate|calculator|compute|what is|what's)\s+(.+)$", spoken)
    if calc_match:
        result = calculate_voice_expression(calc_match.group(0))
        if result is not None:
            actions.append((calc_match.start(), "calculate", result))

    actions.sort(key=lambda item: item[0])
    compact = []
    for _, kind, value in actions:
        if compact and compact[-1][0] == kind and compact[-1][1] == value:
            continue
        compact.append((kind, value))

    if len(compact) < 2:
        return False

    print(f"[voice] long command actions: {compact}")
    send_voice_status(ser, "Long command", f"{len(compact)} steps")

    opened_browser = False
    for kind, value in compact:
        if kind == "open_app":
            if value == "notepad":
                subprocess.Popen(["notepad.exe"])
                time.sleep(0.8)
            elif value == "calculator":
                subprocess.Popen(["calc.exe"])
                time.sleep(0.8)
            elif value == "settings":
                os.startfile("ms-settings:")
                time.sleep(0.8)
            else:
                handle_voice_app_open(ser, value)
                time.sleep(1.0 if value in ("chrome", "word", "excel", "powerpoint") else 0.5)
            opened_browser = opened_browser or value == "chrome"
        elif kind == "new_tabs":
            for _ in range(max(1, int(value))):
                press_hotkey(VK_CONTROL, VK_T)
                time.sleep(0.05)
            opened_browser = True
        elif kind == "search":
            if opened_browser:
                navigate_current_tab(value)
            else:
                open_chrome_target(value)
            time.sleep(0.3)
        elif kind == "type":
            paste_voice_text_and_learn(ser, prepare_voice_typing_text(value))
            time.sleep(0.1)
        elif kind == "calculate":
            paste_text(str(value) + " ")
            time.sleep(0.1)

    send_voice_status(ser, "Long command", "Done")
    return True


def handle_browser_intent_command(ser, spoken):
    """Handle loose multi-intent browser phrases in one sentence."""
    if re.search(r"\b(?:open\s+)?(?:gmail|google mail)(?:\s+(?:in|on)\s+chrome|\s+chrome)?\b", spoken):
        try:
            open_chrome_target("gmail")
            send_voice_status(ser, "Opening", "Gmail")
        except Exception as e:
            print(f"[voice] Gmail browser intent failed: {e}")
            send_voice_status(ser, "Open failed", "Gmail")
        return True

    has_browser = any(word in spoken for word in ("chrome", "browser"))
    has_tabs = "new tab" in spoken
    has_search = re.search(r"\b(search for|search|google|look up|go to|open)\b", spoken)
    if not (has_browser and (has_tabs or has_search)):
        return False

    count = count_before_phrase(spoken, "new tabs", None)
    if count is None and "new tab" in spoken:
        count = 1
    if count is None:
        count = 0

    target = ""
    for pattern in (r"(?:last tab\s+)?(?:search for|search|google|look up|go to)\s+(.+)$",):
        match = re.search(pattern, spoken)
        if match:
            target = match.group(1).strip()
            break

    try:
        if has_browser:
            handle_voice_app_open(ser, "chrome")
            time.sleep(1.2)
        for _ in range(max(0, count)):
            press_hotkey(VK_CONTROL, VK_T)
            time.sleep(0.05)
        if target:
            navigate_current_tab(target)
        if has_tabs and not target and count == 1:
            press_hotkey(VK_CONTROL, VK_L)
            time.sleep(0.08)
            arm_voice_type_next(ser, "New tab", "Speak text")
        else:
            send_voice_status(ser, "Browser command", "Done")
        return True
    except Exception as e:
        print(f"[voice] browser intent failed: {e}")
        send_voice_status(ser, "Command failed", "Browser")
        return True


def split_multi_command(spoken):
    """Split simple chained commands while leaving dictation/search text intact."""
    if re.search(r"\b(and then|then)\b", spoken):
        parts = [p.strip(" ,.;") for p in re.split(r"\b(?:and then|then)\b", spoken) if p.strip(" ,.;")]
        return parts if len(parts) > 1 else []

    if spoken.startswith(("type ", "write ", "text ", "dictate ")) or calculate_voice_expression(spoken):
        return []

    starter = r"(?:open|launch|start|search|google|look up|gmail|compose|new email|write email|send email|inbox|sent mail|drafts|new tab|close tab|close app|close window|type|write|volume|brightness|mute|play|pause|select all|copy|paste|cut|print|screenshot|take screenshot|undo|redo|save|minimize|maximize)\b"
    parts = [p.strip(" ,.;") for p in re.split(r"\s+and\s+(?=" + starter + r")", spoken) if p.strip(" ,.;")]
    return parts if len(parts) > 1 else []


def handle_voice_command(ser, text):
    """Map recognized speech text to PC actions and ESP32 voice status."""
    global voice_typing_enabled, voice_type_next_utterance, last_voice_text, last_calc_result

    spoken = normalize_voice_command(text)
    if not spoken:
        return

    send_voice_status(ser, "Heard", spoken)

    if spoken in ("repeat that", "repeat last", "type last"):
        if last_voice_text:
            typed = prepare_voice_typing_text(last_voice_text)
            paste_voice_text_and_learn(ser, typed)
            send_voice_status(ser, "Repeated", typed)
        else:
            send_voice_status(ser, "No previous", "Try again")
        return

    if spoken in ("cancel voice typing", "cancel typing", "cancel"):
        voice_type_next_utterance = False
        clear_voice_text_context()
        send_voice_status(ser, "Canceled", "No action")
        return

    if voice_type_next_utterance:
        voice_type_next_utterance = False
        last_voice_text = text
        typed = prepare_voice_typing_text(text)
        paste_voice_text_and_learn(ser, typed)
        send_voice_status(ser, "Typed", typed)
        return

    last_voice_text = text

    if should_type_in_recent_text_context(spoken):
        typed = prepare_voice_typing_text(text)
        paste_voice_text_and_learn(ser, typed)
        send_voice_status(ser, "Typed", typed)
        print(f"[voice] typed in recent text context: {typed}")
        return

    payload = voice_command_payload(
        text,
        spoken,
        ("type", "write", "text", "dictate", "type this", "write this", "insert"),
    )
    if payload is not None:
        if not payload:
            arm_voice_type_next(ser, "Type ready", "Speak text next")
            return
        typed = prepare_voice_typing_text(payload)
        paste_voice_text_and_learn(ser, typed)
        send_voice_status(ser, "Typed", typed)
        return

    payload = voice_command_payload_anywhere(
        spoken,
        ("type this", "write this", "type", "write", "text", "dictate", "insert"),
    )
    if payload is not None:
        typed = prepare_voice_typing_text(payload)
        paste_voice_text_and_learn(ser, typed)
        send_voice_status(ser, "Typed", typed)
        return

    if handle_editing_command(ser, spoken):
        return

    text_input_active, text_input_class = focused_text_input_info()
    if should_type_in_text_field(spoken, text_input_active) and not voice_typing_enabled:
        if handle_auto_text_field_command(ser, spoken):
            return
        typed = prepare_voice_typing_text(text)
        paste_voice_text_and_learn(ser, typed)
        send_voice_status(ser, "Auto typing", typed)
        print(f"[voice] auto typing in text field ({text_input_class}): {typed}")
        return

    if handle_weather_voice_command(ser, spoken):
        return

    if handle_office_command(ser, spoken):
        return

    if handle_gmail_command(ser, spoken):
        return

    if handle_camera_command(ser, spoken):
        return

    if handle_browser_intent_command(ser, spoken):
        return

    if handle_camera_command(ser, spoken):
        return

    if handle_voice_app_open(ser, spoken):
        return

    if voice_typing_enabled:
        if spoken in ("stop voice typing", "stop voice type", "stop typing", "stop dictation", "typing mode off", "dictation off"):
            voice_typing_enabled = False
            send_voice_status(ser, "Typing off", "Voice commands")
            return
        typed = prepare_voice_typing_text(text)
        paste_voice_text_and_learn(ser, typed)
        send_voice_status(ser, "Typing", typed)
        return

    if handle_long_intent_sequence(ser, spoken):
        return

    if handle_chrome_tabs_search_command(ser, spoken):
        return

    if handle_gmail_command(ser, spoken):
        return

    if handle_camera_command(ser, spoken):
        return

    if handle_browser_intent_command(ser, spoken):
        return

    if handle_excel_command(ser, spoken):
        return

    multi_parts = split_multi_command(spoken)
    if multi_parts and not spoken.startswith(("type ", "write ", "text ", "dictate ")):
        send_voice_status(ser, "Multi command", f"{len(multi_parts)} steps")
        for part in multi_parts:
            handle_voice_command(ser, part)
            time.sleep(0.25)
        return

    if spoken in ("start voice typing", "start voice type", "start typing", "start dictation", "typing mode on", "dictation on"):
        voice_typing_enabled = True
        send_voice_status(ser, "Typing on", "Speak text")
        return

    if spoken in ("stop voice typing", "stop voice type", "stop typing", "stop dictation", "typing mode off", "dictation off"):
        voice_typing_enabled = False
        send_voice_status(ser, "Typing off", "Voice commands")
        return

    payload = voice_command_payload(
        text,
        spoken,
        ("type", "write", "text", "dictate", "type this", "write this", "insert"),
    )
    if payload is not None:
        if not payload:
            arm_voice_type_next(ser, "Type ready", "Speak text next")
            return
        typed = prepare_voice_typing_text(payload)
        paste_voice_text_and_learn(ser, typed)
        send_voice_status(ser, "Typed", typed)
        return

    payload = voice_command_payload_anywhere(
        spoken,
        ("type this", "write this", "type", "write", "text", "dictate", "insert"),
    )
    if payload is not None:
        typed = prepare_voice_typing_text(payload)
        paste_voice_text_and_learn(ser, typed)
        send_voice_status(ser, "Typed", typed)
        return

    if is_type_arm_command(spoken):
        arm_voice_type_next(ser, "Type ready", "Speak text next")
        return

    if handle_window_command(ser, spoken):
        return

    if handle_camera_command(ser, spoken):
        return

    if handle_voice_app_open(ser, spoken):
        return

    search_payload = voice_command_payload(
        text,
        spoken,
        ("search", "search for", "google", "look up", "find", "browse"),
    )
    if search_payload is not None:
        if not search_payload:
            send_voice_status(ser, "Search what?", "Say search term")
            return
        try:
            open_chrome_target(search_payload)
            send_voice_status(ser, "Searching", search_payload)
        except Exception as e:
            print(f"[voice] search failed: {e}")
            send_voice_status(ser, "Search failed", "Check browser")
        return

    if spoken in ("calculator", "open calculator", "launch calculator", "start calculator"):
        if focus_or_launch_calculator():
            send_voice_status(ser, "Opening", "Calculator")
        else:
            send_voice_status(ser, "Open failed", "Calculator")
        return

    math_result = calculate_from_last_result(spoken)
    if math_result is not None:
        focus_or_launch_calculator()
        paste_text(math_result + " ")
        send_voice_status(ser, "Calculator", math_result)
        print(f"[calc] previous result -> {spoken} = {math_result}")
        return

    math_result = calculate_chained_voice_expression(spoken)
    if math_result is not None:
        focus_or_launch_calculator()
        parsed_result = parse_calc_result(math_result)
        if parsed_result is not None:
            last_calc_result = parsed_result
        paste_text(math_result + " ")
        send_voice_status(ser, "Calculator", math_result)
        print(f"[calc] {spoken} = {math_result}")
        return

    if looks_like_calculator_command(spoken):
        send_voice_status(ser, "Calc failed", "Try: 10 x 20")
        print(f"[calc] could not parse: {spoken}")
        return

    set_volume = re.search(r"(set volume|volume)\s+(?:to\s+)?(\d{1,3})", spoken)
    if set_volume:
        target = max(0, min(100, int(set_volume.group(2))))
        set_volume_target(target)
        send_voice_status(ser, "Volume", f"{target}%")
        return

    if "volume up" in spoken or "increase volume" in spoken:
        change_volume(10)
        send_voice_status(ser, "Volume up", f"{pc_volume_estimate}%")
        return

    if "volume down" in spoken or "decrease volume" in spoken:
        change_volume(-10)
        send_voice_status(ser, "Volume down", f"{pc_volume_estimate}%")
        return

    set_brightness = re.search(r"(set brightness|brightness)\s+(?:to\s+)?(\d{1,3})", spoken)
    if set_brightness:
        target = max(0, min(100, int(set_brightness.group(2))))
        if set_brightness_target(target):
            send_voice_status(ser, "Brightness", f"{target}%")
        else:
            send_voice_status(ser, "Brightness", "Not supported")
        return

    if (
        "brightness up" in spoken or "increase brightness" in spoken or
        "brighter" in spoken or "screen brighter" in spoken
    ):
        ok, target = change_brightness(10)
        send_voice_status(ser, "Brightness up", f"{target}%" if ok else "Not supported")
        return

    if (
        "brightness down" in spoken or "decrease brightness" in spoken or
        "lower brightness" in spoken or "screen dimmer" in spoken or
        "dimmer" in spoken
    ):
        ok, target = change_brightness(-10)
        send_voice_status(ser, "Brightness down", f"{target}%" if ok else "Not supported")
        return

    if (
        "power saver on" in spoken or "battery saver on" in spoken or
        "energy saver on" in spoken or "turn on energy saver" in spoken or "enable energy saver" in spoken or
        "turn on power saver" in spoken or "enable power saver" in spoken or
        "turn on battery saver" in spoken or "enable battery saver" in spoken
    ):
        plan_ok = set_power_saver(True)
        energy_ok = set_windows_energy_saver(True)
        send_voice_status(ser, "Energy saver", "On" if (plan_ok or energy_ok) else "Failed")
        return

    if (
        "power saver off" in spoken or "battery saver off" in spoken or
        "energy saver off" in spoken or "turn off energy saver" in spoken or "disable energy saver" in spoken or
        "turn off power saver" in spoken or "disable power saver" in spoken or
        "turn off battery saver" in spoken or "disable battery saver" in spoken or
        "balanced mode" in spoken
    ):
        energy_ok = set_windows_energy_saver(False)
        plan_ok = set_power_saver(False)
        send_voice_status(ser, "Energy saver", "Off" if (plan_ok or energy_ok) else "Failed")
        return

    if (
        "mute" in spoken or "unmute" in spoken or
        "mute volume" in spoken or "mute sound" in spoken or
        "unmute volume" in spoken or "unmute sound" in spoken
    ):
        send_media_key(TAP_MUTE)
        send_voice_status(ser, "Mute", "Toggled")
        return

    if "play" in spoken or "pause" in spoken:
        send_media_key(TAP_PLAY_PAUSE)
        send_voice_status(ser, "Media", "Play/Pause")
        return

    if "next" in spoken and ("song" in spoken or "track" in spoken or "music" in spoken):
        send_media_key(TAP_NEXT)
        send_voice_status(ser, "Media", "Next track")
        return

    if ("previous" in spoken or "back" in spoken) and ("song" in spoken or "track" in spoken or "music" in spoken):
        send_media_key(TAP_PREV)
        send_voice_status(ser, "Media", "Previous")
        return

    open_payload = voice_command_payload(text, spoken, ("open", "launch", "start"))
    if open_payload is not None:
        if handle_voice_app_open(ser, open_payload):
            return
        try:
            open_chrome_target(open_payload)
            send_voice_status(ser, "Searching", open_payload)
        except Exception as e:
            print(f"[voice] open/search failed: {e}")
            send_voice_status(ser, "Open failed", "Try again")
        return

    if len(spoken.split()) >= 7:
        typed = prepare_voice_typing_text(text)
        paste_voice_text_and_learn(ser, typed)
        send_voice_status(ser, "Typed", typed)
        print(f"[voice] fallback typed long sentence: {typed}")
        return

    send_voice_status(ser, "No match", "Try search or type")
    print(f"[voice] no command matched: {spoken}")


def recognize_google_audio(recognizer, audio):
    """Recognize one AudioData block."""
    return recognizer.recognize_google(audio)


def recognize_long_audio(recognizer, raw_audio, sample_rate, chunk_seconds=12.0):
    """Recognize long recordings in smaller chunks, then join them."""
    bytes_per_second = sample_rate * 2
    chunk_size = int(chunk_seconds * bytes_per_second)
    if len(raw_audio) <= chunk_size:
        return recognize_google_audio(recognizer, sr.AudioData(raw_audio, sample_rate, 2))

    parts = []
    total_chunks = (len(raw_audio) + chunk_size - 1) // chunk_size
    print(f"[voice] long dictation: recognizing {total_chunks} chunks")
    had_request_error = None
    for idx, pos in enumerate(range(0, len(raw_audio), chunk_size), start=1):
        chunk = raw_audio[pos:pos + chunk_size]
        if len(chunk) < bytes_per_second // 2:
            continue
        try:
            text = recognize_google_audio(recognizer, sr.AudioData(chunk, sample_rate, 2))
            if text:
                parts.append(text.strip())
                print(f"[voice] chunk {idx}/{total_chunks}: {text}")
        except sr.UnknownValueError:
            print(f"[voice] chunk {idx}/{total_chunks}: not understood")
            continue
        except sr.RequestError as e:
            had_request_error = e
            print(f"[voice] chunk {idx}/{total_chunks}: service error {e}")
            continue

    if not parts:
        if had_request_error:
            raise had_request_error
        raise sr.UnknownValueError()
    return " ".join(parts)


def warm_sounddevice_input():
    """Open the mic briefly so the first real press is less likely to miss audio."""
    global microphone_warmed
    if microphone_warmed or not HAS_SOUNDDEVICE:
        return
    microphone_warmed = True
    try:
        def callback(indata, frame_count, time_info, status):
            (void_indata, void_frame_count, void_time_info, void_status) = (
                indata, frame_count, time_info, status
            )

        with MIC_STREAM_LOCK:
            with sd.InputStream(samplerate=16000, channels=1, dtype="int16", callback=callback):
                time.sleep(0.25)
        print("[voice] microphone warmed")
    except Exception as e:
        print(f"[voice] microphone warm-up skipped: {e}")


def warm_sounddevice_input_async():
    if HAS_SOUNDDEVICE:
        threading.Thread(target=warm_sounddevice_input, daemon=True).start()


def recognize_sounddevice_until_release(recognizer, stop_event, max_seconds=60.0, sample_rate=16000, ready_callback=None):
    """Record while the ESP32 Mic button is held, then recognize after release."""
    if not HAS_SOUNDDEVICE:
        raise RuntimeError("sounddevice is not installed")

    frames = []
    ready = threading.Event()

    def callback(indata, frame_count, time_info, status):
        (void_frame_count, void_time_info) = (frame_count, time_info)
        if status:
            print(f"[voice] input status: {status}")
        frames.append(indata.copy().tobytes())
        ready.set()

    with MIC_STREAM_LOCK:
        with sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16", callback=callback):
            started = time.time()
            if ready.wait(timeout=0.8) and ready_callback:
                ready_callback()
            while not stop_event.is_set() and time.time() - started < max_seconds:
                time.sleep(0.03)

    duration = time.time() - started
    if not frames or duration < 0.25:
        raise sr.UnknownValueError()

    raw_audio = b"".join(frames)
    return recognize_long_audio(recognizer, raw_audio, sample_rate)


def voice_worker(ser, stop_event):
    """Listen on the PC microphone and control apps while updating the ESP32."""
    if not HAS_SPEECH:
        print("[voice] Install voice support with: pip install SpeechRecognition sounddevice")
        send_voice_status(ser, "Voice disabled", "Install packages")
        return

    recognizer = sr.Recognizer()

    if HAS_SOUNDDEVICE:
        print("[voice] Hold Mic, speak, then release to process.")
        send_voice_status(ser, "Starting mic", "Please wait")
        try:
            text = recognize_sounddevice_until_release(
                recognizer,
                stop_event,
                ready_callback=lambda: send_voice_status(ser, "Listening", "Hold Mic"),
            )
            print(f"[voice] {text}")
            handle_voice_command(ser, text)
            time.sleep(0.2)
            if voice_typing_enabled:
                send_voice_status(ser, "Typing ready", "Hold Mic")
            else:
                send_voice_status(ser, "Voice off", "Hold Mic")
        except sr.UnknownValueError:
            send_voice_status(ser, "Didn't catch", "Hold Mic again")
        except sr.RequestError as e:
            print(f"[voice] recognition service failed: {e}")
            send_voice_status(ser, "Voice offline", "Need internet")
        except Exception as e:
            print(f"[voice] sounddevice listen failed: {e}")
            send_voice_status(ser, "Mic error", "Check input")
        return

    mic = None
    try:
        mic = sr.Microphone()
    except Exception as e:
        print(f"[voice] microphone unavailable: {e}")
        print("[voice] Install fallback mic support with: pip install sounddevice numpy")
        send_voice_status(ser, "Mic unavailable", "Install sounddevice")
        return

    print("[voice] PyAudio fallback active. It may stop at silence before release.")
    send_voice_status(ser, "Listening", "Speak command")

    with mic as source:
        try:
            recognizer.adjust_for_ambient_noise(source, duration=0.6)
        except Exception:
            pass

        while not stop_event.is_set():
            try:
                audio = recognizer.listen(source, timeout=1.0, phrase_time_limit=3.5)
            except sr.WaitTimeoutError:
                continue
            except Exception as e:
                print(f"[voice] listen failed: {e}")
                time.sleep(1)
                continue

            try:
                text = recognizer.recognize_google(audio)
                print(f"[voice] {text}")
                handle_voice_command(ser, text)
                time.sleep(0.2)
                if voice_typing_enabled:
                    send_voice_status(ser, "Typing on", "Speak text")
                else:
                    send_voice_status(ser, "Listening", "Say a command")
            except sr.UnknownValueError:
                send_voice_status(ser, "Didn't catch", "Try again")
            except sr.RequestError as e:
                print(f"[voice] recognition service failed: {e}")
                send_voice_status(ser, "Voice offline", "Need internet")
                time.sleep(3)


def assistant_worker(ser, stop_event):
    """Separate assistant listener; does not run voice typing/control commands."""
    if not HAS_SPEECH:
        print("[assistant] Install voice support with: pip install SpeechRecognition sounddevice")
        send_assistant_status(ser, "Voice disabled")
        return

    recognizer = sr.Recognizer()
    if HAS_SOUNDDEVICE:
        print("[assistant] Hold AI, ask, then release.")
        send_assistant_status(ser, "Starting mic")
        try:
            text = recognize_sounddevice_until_release(
                recognizer,
                stop_event,
                ready_callback=lambda: send_assistant_status(ser, "Listening"),
            )
            print(f"[assistant] {text}")
            send_assistant_status(ser, "Processing")
            handle_assistant_command(ser, text)
        except sr.UnknownValueError:
            send_assistant_status(ser, "Didn't catch")
        except sr.RequestError as e:
            print(f"[assistant] recognition service failed: {e}")
            send_assistant_status(ser, "Need internet")
        except Exception as e:
            print(f"[assistant] listen failed: {e}")
            send_assistant_status(ser, "Mic error")
        return

    send_assistant_status(ser, "Mic unavailable")


def start_voice_thread(ser, current_thread, current_stop):
    """Start voice listening if it is not already running."""
    if current_thread and current_thread.is_alive():
        send_voice_status(ser, "Listening", "Release to finish")
        return current_thread, current_stop

    stop_event = threading.Event()
    thread = threading.Thread(target=voice_worker, args=(ser, stop_event), daemon=True)
    thread.start()
    return thread, stop_event


def start_assistant_thread(ser, current_thread, current_stop):
    if current_thread and current_thread.is_alive():
        send_assistant_status(ser, "Listening")
        return current_thread, current_stop

    stop_event = threading.Event()
    thread = threading.Thread(target=assistant_worker, args=(ser, stop_event), daemon=True)
    thread.start()
    return thread, stop_event


def stop_assistant_thread(ser, current_thread, current_stop):
    if current_thread and current_thread.is_alive():
        if current_stop.is_set():
            send_assistant_status(ser, "Processing")
            return current_thread, current_stop

        def delayed_stop():
            send_assistant_status(ser, "Finishing")
            time.sleep(1.2)
            if not current_stop.is_set():
                current_stop.set()
            send_assistant_status(ser, "Processing")

        threading.Thread(target=delayed_stop, daemon=True).start()
        return current_thread, current_stop

    send_assistant_status(ser, "Ready")
    return None, threading.Event()


def stop_voice_thread(ser, current_thread, current_stop):
    """Stop voice listening and update the ESP32 status widget."""
    if current_thread and current_thread.is_alive():
        if current_stop.is_set():
            send_voice_status(ser, "Processing", "Please wait")
            return current_thread, current_stop

        def delayed_stop():
            send_voice_status(ser, "Finishing", "One moment")
            time.sleep(1.2)
            if not current_stop.is_set():
                current_stop.set()
            send_voice_status(ser, "Processing", "Mic released")

        threading.Thread(target=delayed_stop, daemon=True).start()
        return current_thread, current_stop

    send_voice_status(ser, "Voice off", "Hold Mic")
    return None, threading.Event()


def read_serial_line(ser, timeout=10.0):
    """Read one newline-terminated serial line without entering the main loop."""
    end_time = time.time() + timeout
    buf = ""
    while time.time() < end_time:
        ch = ser.read(1).decode("utf-8", errors="ignore")
        if not ch:
            continue
        if ch in ("\n", "\r"):
            line = buf.strip()
            if line:
                print(f"[rx] {line}")
                return line
            buf = ""
        else:
            buf += ch
    return None


def wait_for_serial_line(ser, expected, timeout=10.0):
    """Wait for one of the expected serial lines, ignoring unrelated messages."""
    expected = set(expected)
    end_time = time.time() + timeout
    while time.time() < end_time:
        line = read_serial_line(ser, timeout=max(0.2, min(2.0, end_time - time.time())))
        if line is None:
            continue
        if line in expected:
            return line
        print(f"[bg] ignoring while uploading: {line}")
    return None


def image_to_rgb565_bytes(path):
    """Resize/crop an image to the ESP32 screen and return LVGL RGB565 bytes."""
    if not HAS_PIL:
        raise RuntimeError("Pillow is not installed. Run: pip install pillow")

    img = Image.open(path).convert("RGB")
    img = ImageOps.fit(img, (SCREEN_W, SCREEN_H), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))

    out = bytearray(BG_RAW_SIZE)
    j = 0
    for r, g, b in img.getdata():
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[j] = (rgb565 >> 8) & 0xFF
        out[j + 1] = rgb565 & 0xFF
        j += 2
    return bytes(out)


def upload_background(ser, image_path):
    """Send a custom background image to the ESP32."""
    if not image_path:
        return

    image_path = Path(image_path)
    if not image_path.exists():
        print(f"[bg] image not found: {image_path}")
        return

    print(f"[bg] preparing {image_path} ...")
    try:
        data = image_to_rgb565_bytes(image_path)
    except Exception as e:
        print(f"[bg] failed to prepare image: {e}")
        return

    send_line(ser, f"BGIMG {len(data)}")
    reply = wait_for_serial_line(ser, {"BGREADY", "BGERR"}, timeout=8)
    if reply != "BGREADY":
        print("[bg] ESP32 did not accept background upload.")
        return

    print("[bg] uploading background ...")
    chunk_size = 256
    start = time.time()
    for pos in range(0, len(data), chunk_size):
        ser.write(data[pos:pos + chunk_size])
        if pos % (16 * 1024) == 0:
            print(f"[bg] {pos * 100 // len(data)}%")
        time.sleep(0.01)
    ser.flush()
    print("[bg] 100%")

    reply = wait_for_serial_line(ser, {"BGDONE", "BGERR"}, timeout=120)
    if reply == "BGDONE":
        print(f"[bg] background image saved and applied in {time.time() - start:.1f}s.")
    else:
        print("[bg] upload did not finish cleanly.")


def field(value, max_len=24):
    text = str(value or "").replace("|", "/").replace("\n", " ").replace("\r", " ")
    return text[:max_len]


def send_home_config(ser):
    """Push background, widget, and app layout customizations to the board."""
    bg = CONFIG.get("background", {})
    color = str(bg.get("color", DEFAULT_CONFIG["background"]["color"])).replace("#", "")[:6]
    send_line(ser, f"BG {color}")

    for idx, widget in enumerate(CONFIG["widgets"]):
        send_line(
            ser,
            "WIDGET "
            f"{idx}|{field(widget.get('title'), 14)}|"
            f"{field(widget.get('line1'), 16)}|{field(widget.get('line2'), 18)}",
        )

    for idx, app in enumerate(APPS[:6]):
        color = str(app.get("color", "636366")).replace("#", "")[:6]
        send_line(
            ser,
            "APP "
            f"{idx}|{field(app.get('name'), 16)}|"
            f"{field(app.get('abbr'), 4)}|{color}",
        )


def send_time(ser):
    t_now = datetime.datetime.now()
    send_line(ser, f"TIME {t_now.strftime('%H:%M')}")


def send_weather(ser):
    temp, cond = get_weather()
    send_line(ser, f"WEATHER {temp} {cond}")


def connect_serial(preferred_port=None):
    """Connect to the board, retrying until it appears."""
    while True:
        port = preferred_port or find_port()
        if not port:
            print("[serial] No COM port found. Plug in the Super Mouse...")
            time.sleep(2)
            continue

        print(f"[serial] Connecting to Super Mouse on {port} ...")
        try:
            ser = serial.Serial(port, 115200, timeout=0.2, write_timeout=5)
            time.sleep(2.0)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            print("[serial] Connected.")
            return ser
        except serial.SerialException as e:
            print(f"[serial] Cannot open {port}: {e}")
            if "PermissionError" in str(e) or "Access is denied" in str(e):
                print("[serial] Close ESP-IDF Monitor with Ctrl+] and stop any other launcher using this COM port.")
            if preferred_port:
                print("[serial] Waiting for the same COM port to come back...")
            time.sleep(2)


def parse_args():
    preferred_port = None
    background_path = None
    voice_enabled = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--voice":
            voice_enabled = True
            i += 1
        elif arg in ("--background", "--bg"):
            if i + 1 >= len(args):
                raise SystemExit("--background needs an image path")
            background_path = args[i + 1]
            i += 2
        elif arg.startswith("--background="):
            background_path = arg.split("=", 1)[1]
            i += 1
        elif arg.startswith("--bg="):
            background_path = arg.split("=", 1)[1]
            i += 1
        else:
            preferred_port = arg
            i += 1

    cfg_bg = CONFIG.get("background", {}).get("image")
    if not background_path and cfg_bg and cfg_bg != "aurora":
        background_path = cfg_bg
    return preferred_port, background_path, voice_enabled


def main():
    preferred_port, background_path, voice_enabled = parse_args()
    background_uploaded = False

    while True:
        ser = connect_serial(preferred_port)
        voice_stop = threading.Event()
        voice_thread = None
        assistant_stop = threading.Event()
        assistant_thread = None
        buf = ""
        last_time = 0.0
        last_weather = 0.0
        last_assistant_check = 0.0
        last_notes_email_check = 0.0
        keyboard_composing = ""
        keyboard_previous_word = ""

        try:
            print("[serial] Waiting for board to settle...")
            time.sleep(1.0)
            send_home_config(ser)
            send_keyboard_words(ser)
            send_time(ser)
            send_weather(ser)
            warm_sounddevice_input_async()
            if background_path and not background_uploaded:
                upload_background(ser, background_path)
                background_uploaded = True
            if voice_enabled:
                voice_thread, voice_stop = start_voice_thread(ser, voice_thread, voice_stop)
            last_time = time.time()
            last_weather = last_time
            last_assistant_check = last_time
            last_notes_email_check = last_time

            while True:
                now = time.time()
                if now - last_time >= 30:
                    send_time(ser)
                    last_time = now

                if now - last_weather >= 600:
                    send_weather(ser)
                    last_weather = now

                if now - last_assistant_check >= 5:
                    check_assistant_alerts(ser)
                    last_assistant_check = now

                if now - last_notes_email_check >= 60:
                    check_notes_summary_schedule(ser)
                    last_notes_email_check = now

                ch = ser.read(1).decode("utf-8", errors="ignore")
                if not ch:
                    continue

                if ch in ("\n", "\r"):
                    line = buf.strip()
                    buf = ""
                    if not line:
                        continue

                    print(f"[rx] {line}")

                    if line == "READY":
                        send_home_config(ser)
                        send_keyboard_words(ser)
                        send_time(ser)
                        send_weather(ser)
                        last_time = time.time()
                        last_weather = last_time

                    elif line.startswith("TAP "):
                        try:
                            idx = int(line.split()[1])
                            if idx == TAP_VOICE_START:
                                voice_thread, voice_stop = start_voice_thread(ser, voice_thread, voice_stop)
                            elif idx == TAP_VOICE_STOP:
                                voice_thread, voice_stop = stop_voice_thread(ser, voice_thread, voice_stop)
                            elif idx == TAP_ASSIST_START:
                                assistant_thread, assistant_stop = start_assistant_thread(ser, assistant_thread, assistant_stop)
                            elif idx == TAP_ASSIST_STOP:
                                assistant_thread, assistant_stop = stop_assistant_thread(ser, assistant_thread, assistant_stop)
                            elif idx in (TAP_PLAY_PAUSE, TAP_PREV, TAP_NEXT, TAP_MUTE):
                                send_media_key(idx)
                            else:
                                launch_app(idx)
                        except (ValueError, IndexError):
                            pass

                    elif line.startswith("VOL "):
                        try:
                            set_volume_target(int(line.split()[1]))
                        except (ValueError, IndexError):
                            pass

                    elif line.startswith("MOVE "):
                        try:
                            _, dx, dy = line.split()
                            move_mouse(int(dx), int(dy))
                        except (ValueError, IndexError):
                            pass

                    elif line.startswith("CLICK "):
                        try:
                            click_mouse(int(line.split()[1]))
                        except (ValueError, IndexError):
                            pass

                    elif line.startswith("TEXT "):
                        text = line[5:]
                        if text == "{BACKSPACE}":
                            tap_key(VK_BACK)
                            keyboard_composing = keyboard_composing[:-1]
                        elif text == "{CLEAR}":
                            press_hotkey(VK_CONTROL, VK_A)
                            time.sleep(0.03)
                            tap_key(VK_BACK)
                            keyboard_composing = ""
                            keyboard_previous_word = ""
                        elif text == "{ENTER}":
                            if keyboard_composing:
                                learn_keyboard_word(keyboard_composing, ser)
                                if keyboard_previous_word:
                                    learn_keyboard_pair(keyboard_previous_word, keyboard_composing, ser)
                            tap_key(VK_RETURN)
                            keyboard_composing = ""
                            keyboard_previous_word = ""
                        elif text == "{SPACE}":
                            if keyboard_composing:
                                learn_keyboard_word(keyboard_composing, ser)
                                if keyboard_previous_word:
                                    learn_keyboard_pair(keyboard_previous_word, keyboard_composing, ser)
                                keyboard_previous_word = normalize_keyboard_word(keyboard_composing)
                            paste_text(" ")
                            keyboard_composing = ""
                        else:
                            paste_text(text)
                            if len(text) == 1 and text.isalnum():
                                keyboard_composing += text.lower()
                            else:
                                keyboard_composing = ""
                        print(f"[keyboard] typed: {text}")

                    elif line.startswith("WORD "):
                        word = line[5:]
                        for _ in range(len(keyboard_composing)):
                            tap_key(VK_BACK)
                        paste_text(word)
                        tap_key(VK_SPACE)
                        learn_keyboard_word(word, ser)
                        if keyboard_previous_word:
                            learn_keyboard_pair(keyboard_previous_word, word, ser)
                        keyboard_previous_word = normalize_keyboard_word(word)
                        print(f"[keyboard] word+space: {word}")
                        keyboard_composing = ""

                    elif line == "PONG":
                        pass
                else:
                    buf += ch

        except KeyboardInterrupt:
            print("\nExiting.")
            voice_stop.set()
            assistant_stop.set()
            ser.close()
            return
        except (OSError, serial.SerialException) as e:
            voice_stop.set()
            assistant_stop.set()
            print(f"[serial] Disconnected: {e}")
            try:
                ser.close()
            except Exception:
                pass
            print("[serial] Reconnect the cable; I will keep trying.")
            time.sleep(2)


if __name__ == "__main__":
    main()
