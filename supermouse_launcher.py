"""
supermouse_launcher.py — Super Mouse PC companion
Requires: pip install pyserial requests

Usage:
    python supermouse_launcher.py          # auto-detect COM port
    python supermouse_launcher.py COM3     # specify port manually
"""

import sys
import os
import time
import subprocess
import datetime
import ctypes
import serial
import serial.tools.list_ports

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("WARNING: 'requests' not installed. Weather disabled. Run: pip install requests")

# ── App launcher map (index matches APPS[] in lvgl_ui.cpp) ──────────
APPS = [
    {"name": "Chrome",    "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe"},
    {"name": "Word",      "path": r"winword"},
    {"name": "Excel",     "path": r"excel"},
    {"name": "PowerPnt",  "path": r"powerpnt"},
    {"name": "VS Code",   "path": r"code"},
    {"name": "Settings",  "path": r"ms-settings:"},
]

# Media tap codes (must match lvgl_ui.cpp)
TAP_PLAY_PAUSE = 20
TAP_PREV       = 21
TAP_NEXT       = 22


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
    vk = {"PlayPause": 0xB3, "PreviousTrack": 0xB1, "NextTrack": 0xB0}
    key_name = {
        TAP_PLAY_PAUSE: "PlayPause",
        TAP_PREV: "PreviousTrack",
        TAP_NEXT: "NextTrack",
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
    print(f"[tx] {line}")


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
            ser = serial.Serial(port, 115200, timeout=0.2, write_timeout=1)
            time.sleep(1.0)
            ser.reset_input_buffer()
            print("[serial] Connected.")
            return ser
        except serial.SerialException as e:
            print(f"[serial] Cannot open {port}: {e}")
            if preferred_port:
                print("[serial] Waiting for the same COM port to come back...")
            time.sleep(2)


def main():
    preferred_port = sys.argv[1] if len(sys.argv) > 1 else None

    while True:
        ser = connect_serial(preferred_port)
        buf = ""
        last_time = 0.0
        last_weather = 0.0

        try:
            send_time(ser)
            send_weather(ser)
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
                        send_time(ser)
                        send_weather(ser)
                        last_time = time.time()
                        last_weather = last_time

                    elif line.startswith("TAP "):
                        try:
                            idx = int(line.split()[1])
                            if idx in (TAP_PLAY_PAUSE, TAP_PREV, TAP_NEXT):
                                send_media_key(idx)
                            else:
                                launch_app(idx)
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
