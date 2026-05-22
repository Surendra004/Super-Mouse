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
import uuid
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

# ── App launcher map (index matches APPS[] in lvgl_ui.cpp) ──────────
CONFIG_PATH = Path(__file__).with_name("supermouse_config.json")

DEFAULT_CONFIG = {
    "background": {"color": "181A24", "image": "aurora"},
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
    return merged


CONFIG = load_config()
APPS = CONFIG["apps"]
SCREEN_W = 320
SCREEN_H = 480
BG_RAW_SIZE = SCREEN_W * SCREEN_H * 2

# Media tap codes (must match lvgl_ui.cpp)
TAP_PLAY_PAUSE = 20
TAP_PREV       = 21
TAP_NEXT       = 22
TAP_MUTE       = 32

pc_volume_estimate = 70
pc_volume_synced = False
volume_controller = None
volume_controller_failed = False

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

CLSCTX_ALL = 0x17
E_RENDER = 0
E_CONSOLE = 0


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


def get_weather():
    """Fetch weather from wttr.in (no API key needed)."""
    if not HAS_REQUESTS:
        return "N/A", "No data"
    try:
        r = requests.get("https://wttr.in/?format=%t+%C", timeout=6)
        if r.status_code == 200:
            raw = r.text.strip().replace("+", "")
            parts = raw.split(" ", 1)
            temp = parts[0].replace("\xb0C", "C").replace("\xb0F", "F").strip()
            cond = (parts[1] if len(parts) > 1 else "Clear")[:14].strip()
            return temp, cond
    except Exception as e:
        print(f"[weather] fetch failed: {e}")
    return "N/A", "Unavail"


def send_line(ser, line):
    """Send one newline-terminated command to the board."""
    ser.write(f"{line}\n".encode())
    ser.flush()
    print(f"[tx] {line}")
    time.sleep(0.03)


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
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--background", "--bg"):
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
    return preferred_port, background_path


def main():
    preferred_port, background_path = parse_args()
    background_uploaded = False

    while True:
        ser = connect_serial(preferred_port)
        buf = ""
        last_time = 0.0
        last_weather = 0.0

        try:
            print("[serial] Waiting for board to settle...")
            time.sleep(1.0)
            send_home_config(ser)
            send_time(ser)
            send_weather(ser)
            if background_path and not background_uploaded:
                upload_background(ser, background_path)
                background_uploaded = True
            last_time = time.time()
            last_weather = last_time

            while True:
                now = time.time()
                if now - last_time >= 30:
                    send_time(ser)
                    last_time = now

                if now - last_weather >= 600:
                    send_weather(ser)
                    last_weather = now

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
                        send_time(ser)
                        send_weather(ser)
                        last_time = time.time()
                        last_weather = last_time

                    elif line.startswith("TAP "):
                        try:
                            idx = int(line.split()[1])
                            if idx in (TAP_PLAY_PAUSE, TAP_PREV, TAP_NEXT, TAP_MUTE):
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

                    elif line == "PONG":
                        pass
                else:
                    buf += ch

        except KeyboardInterrupt:
            print("\nExiting.")
            ser.close()
            return
        except (OSError, serial.SerialException) as e:
            print(f"[serial] Disconnected: {e}")
            try:
                ser.close()
            except Exception:
                pass
            print("[serial] Reconnect the cable; I will keep trying.")
            time.sleep(2)


if __name__ == "__main__":
    main()
