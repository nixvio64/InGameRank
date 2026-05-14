import sys
import os
import warnings
warnings.filterwarnings("ignore", message="Microphone state initially", category=UserWarning)
import logging

# suppress dualsense OSError 
_ds_logger = logging.getLogger("dualsense_controller")
_ds_logger.setLevel(logging.CRITICAL)
_ds_logger.addHandler(logging.NullHandler())
_ds_logger.propagate = False  

class _StreamFilter:
    def __init__(self, stream):
        self.stream = stream
        self._drop_next_newline = False

    def write(self, data):
        if isinstance(data, str):
            if "An Exception in the loop thread occured" in data or "Failed to read from HID device" in data:
                if not data.endswith('\n'):
                    self._drop_next_newline = True
                return len(data)
            if self._drop_next_newline and data == '\n':
                self._drop_next_newline = False
                return len(data)
            self._drop_next_newline = False
        return self.stream.write(data)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, attr):
        return getattr(self.stream, attr)

sys.stderr = _StreamFilter(sys.stderr)
sys.stdout = _StreamFilter(sys.stdout)

import threading
import time
import json
import socket
import keyboard
import win32gui
import urllib.request
import urllib.parse
import signal
import ctypes
import argparse
import webbrowser
import re
import atexit
from ctypes import wintypes
from dualsense_controller import DualSenseController

from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QDialog
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QPixmap
from curl_cffi import requests as cf_requests
import random

class _DualSenseLogFilter(logging.Filter):
    def filter(self, record):
        msg = str(record.msg)
        if "An Exception in the loop thread occured" in msg or "Failed to read from HID device" in msg:
            return False
        if record.exc_info:
            exc_val = str(record.exc_info[1])
            if "Failed to read from HID device" in exc_val:
                return False
        return True

_ds_log_filter = _DualSenseLogFilter()
for _logger in [logging.getLogger()] + list(logging.Logger.manager.loggerDict.values()):
    if isinstance(_logger, logging.Logger):
        for _h in _logger.handlers:
            _h.addFilter(_ds_log_filter)

VERSION = "v1.0.6"
DEBUG = False


IMPERSONATE_OPTIONS = [
    "chrome120", "chrome124",
    "edge99", "edge101",
]
# XInput / Controller Support

class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ =[
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", wintypes.BYTE),
        ("bRightTrigger", wintypes.BYTE),
        ("sThumbLX", wintypes.SHORT),
        ("sThumbLY", wintypes.SHORT),
        ("sThumbRX", wintypes.SHORT),
        ("sThumbRY", wintypes.SHORT),
    ]

class XINPUT_STATE(ctypes.Structure):
    _fields_ =[
        ("dwPacketNumber", wintypes.DWORD),
        ("Gamepad", XINPUT_GAMEPAD),
    ]

try:
    xinput = ctypes.windll.xinput1_4
except OSError:
    try:
        xinput = ctypes.windll.xinput1_3
    except OSError:
        xinput = None

def get_xinput_state(user_index=0):
    if not xinput: return None
    state = XINPUT_STATE()
    if xinput.XInputGetState(user_index, ctypes.byref(state)) == 0:
        return state
    return None

# DualSense Controller Support

ps_controller: None | DualSenseController = None
_ps_pressed = set()
_ps_btns = ["btn_cross", "btn_circle", "btn_square", "btn_triangle",
            "btn_l1", "btn_r1", "btn_l2", "btn_r2",
            "btn_l3", "btn_r3", "btn_options", "btn_create",
            "btn_ps", "btn_touchpad", "btn_mute",
            "btn_up", "btn_down", "btn_left", "btn_right"]

def _ps_make_handler(name):
    def handler(value):
        global _ps_pressed
        if value:
            _ps_pressed.add(name)
        else:
            _ps_pressed.discard(name)
    return handler

def _ps_disconnect_handler(controller):
    def _reconnect_loop():
        global ps_controller, _ps_pressed

        _ps_pressed = set()

        try:
            controller.deactivate()
        except Exception:
            pass


        ps_controller = None
        print("[Controller] Disconnected. Waiting for reconnect...")

    threading.Thread(target=_reconnect_loop, daemon=True).start()

def is_ps_connected() -> bool:
    device_infos = DualSenseController.enumerate_devices()
    if len(device_infos) < 1:
        return False
    return True

def setup_ps_controller() -> None | DualSenseController:
    if not is_ps_connected():
        if DEBUG: print("[DEBUG] setup_ps_controller: No DualSense detected")
        return None # no dualsense available

    controller = DualSenseController()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        controller.activate()

    # bind buttons
    for btn_name in _ps_btns:
        getattr(controller, btn_name).on_change(_ps_make_handler(btn_name))

    controller.on_error(lambda: _ps_disconnect_handler(controller))

    global ps_controller
    ps_controller = controller

    return controller

def _ps_monitor_thread():
    global ps_controller
    while True:
        time.sleep(1)
        if (config.get("is_controller") and config.get("controller_type") == "dualsense" and ps_controller is None):
            result = setup_ps_controller()
            if result:
                print("[Controller] Reconnected!")
                if DEBUG: print("[DEBUG] DualSense connected by monitor thread")

def ps_get_inputs():
    """Returns list of currently held buttons"""
    return list(_ps_pressed)

def _cleanup():
    global ps_controller
    if ps_controller:
        try:
            t = threading.Thread(target=ps_controller.deactivate, daemon=True)
            t.start()
            t.join(timeout=2.0)
        except Exception:
            pass
        ps_controller = None

atexit.register(_cleanup)

def _sigint_handler(sig, frame):
    """Handle CTRL+C: clean up the controller, then hard-exit so nothing can hang."""
    _cleanup()
    os._exit(0)  

signal.signal(signal.SIGINT, _sigint_handler)

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# Config Management

CONFIG_FILE = "config.json"
config = {
    "hotkey": "tab",
    "is_controller": False,
    "controller_type": "xinput",  # "xinput" | "dualsense"
    "controller_button": 0,
    "joy_id": 0,
    "rl_window_title": "Rocket League",
    "rl_host": "127.0.0.1",
    "rl_port": 49123,
    "require_mouse_over_rl_window": True,
}

class BindWorker(QThread):
    finished_bind = Signal(str, bool, int, int, str)

    def run(self):
        time.sleep(0.5) # Prevent registering accidental presses
        pressed_key = None
        
        def on_press(e):
            nonlocal pressed_key
            pressed_key = e.name
            
        keyboard.on_press(on_press)

        setup_ps_controller()

        while True:
            if pressed_key:
                self.finished_bind.emit(pressed_key, False, 0, 0, "keyboard")
                break

            # check XInput
            xi = get_xinput_state()
            if xi and xi.Gamepad.wButtons != 0:
                btn = xi.Gamepad.wButtons
                while get_xinput_state() and get_xinput_state().Gamepad.wButtons != 0:
                    time.sleep(0.05)
                self.finished_bind.emit("", True, btn, 0, "xinput")
                break

            # check DualSense
            found_ds = False
            if ps_controller:
                ps_input_result = ps_get_inputs()
                if ps_input_result:
                    ps_btn = ps_input_result[0]
                    self.finished_bind.emit("", True, _ps_btns.index(ps_btn), 0, "dualsense")
                    found_ds = True

            if found_ds: break

            time.sleep(0.01)
            
        keyboard.unhook_all()

class FirstRunIniDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Action Required")
        self.setFixedSize(480, 240)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        
        layout = QVBoxLayout()
        label = QLabel(
            "<b>First Time Setup: Enable Rocket League API</b><br><br>"
            "Please open and edit the following file:<br>"
            "<b>&lt;Install Dir&gt;\\TAGame\\Config\\DefaultStatsAPI.ini</b><br><br>"
            "Change the field <b>PacketSendRate</b> from <b>0</b> to <b>120</b><br>"
            "<span style='font-size: 11px;'>(20 is recommended)</span><br><br>"
            "<i>Please restart Rocket League if the game is already open.</i>"
        )
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-size: 14px; font-family: Segoe UI;")
        layout.addWidget(label)
        
        btn_layout = QHBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        btn_ok = QPushButton("Got it!")
        btn_ok.setFixedSize(100, 30)
        btn_ok.clicked.connect(self.accept)
        
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)
        self.setLayout(layout)

class SetupDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rocket League Overlay Setup")
        self.setFixedSize(400, 150)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        
        layout = QVBoxLayout()
        label = QLabel("Press any <b>KEYBOARD KEY</b> or <b>CONTROLLER BUTTON</b><br>to bind the overlay hotkey...")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-size: 14px; font-family: Segoe UI;")
        layout.addWidget(label)
        self.setLayout(layout)

        self.worker = BindWorker()
        self.worker.finished_bind.connect(self.on_bind_finished)
        self.worker.start()

    def on_bind_finished(self, key, is_controller, btn, joy_id, controller_type):
        global config
        if is_controller:
            config["is_controller"]     = True
            config["controller_type"]   = controller_type
            config["controller_button"] = btn
            config["joy_id"]            = joy_id
            if controller_type == "dualsense":
                btn_name = _ps_btns[btn]
                print(f"[Overlay] Bound to DualSense controller button: {btn_name}")
            else:
                print(f"[Overlay] Bound to controller button: {btn}")
        else:
            config["is_controller"] = False
            config["hotkey"] = key
            print(f"[Overlay] Bound to keyboard key: {key}")
            
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
        print("[Overlay] Saved to config.json!")
        self.accept()

def load_or_setup_config(force_rebind=False):
    global config
    needs_setup = force_rebind
    is_first_run = False

    if os.path.exists(CONFIG_FILE) and not force_rebind:
        try:
            with open(CONFIG_FILE, "r") as f:
                config.update(json.load(f))
            config.setdefault("controller_type", "xinput")  # backward compat
            config.setdefault("joy_id", 0)
            if config.get("controller_type") == "dualsense":
                setup_ps_controller()
        except Exception as e:
            print(f"[Overlay] Failed to load config: {e}")
            needs_setup = True
            is_first_run = True
    else:
        needs_setup = True
        if not force_rebind:
            is_first_run = True

    if is_first_run:
        ini_dialog = FirstRunIniDialog()
        ini_dialog.exec()

    if needs_setup:
        dialog = SetupDialog()
        dialog.exec()

    # start monitor thread 
    if config.get("is_controller") and config.get("controller_type") == "dualsense":
        threading.Thread(target=_ps_monitor_thread, daemon=True).start()

class UpdateDialog(QDialog):
    def __init__(self, latest_version, url):
        super().__init__()
        self.setWindowTitle("Update Available")
        self.setFixedSize(400, 150)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        
        self.url = url
        
        layout = QVBoxLayout()
        label = QLabel(f"A new update is available version {latest_version}")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-size: 14px; font-family: Segoe UI;")
        layout.addWidget(label)
        
        btn_layout = QHBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        btn_update = QPushButton("Update")
        btn_update.setFixedSize(100, 30)
        btn_update.clicked.connect(self.on_update)
        
        btn_ignore = QPushButton("Ignore")
        btn_ignore.setFixedSize(100, 30)
        btn_ignore.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_update)
        btn_layout.addWidget(btn_ignore)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def on_update(self):
        webbrowser.open(self.url)
        self.accept()

def check_for_updates():
    try:
        url = "https://github.com/nixvio64/InGameRank/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=5) as response:
            final_url = response.geturl()
            
        if "/releases/tag/" in final_url:
            latest_version = final_url.split("/releases/tag/")[-1]
            
            def parse_v(v):
                return tuple(int(i) for i in re.findall(r'\d+', v))
                
            if parse_v(latest_version) > parse_v(VERSION):
                dialog = UpdateDialog(latest_version, final_url)
                dialog.exec()
    except Exception as e:
        if DEBUG: print(f"[DEBUG] Update check failed: {e}")

FONT_NAME = "Segoe UI"
FONT_SIZE = 11
CACHE_TTL = 300  


TRACKER_ATTEMPTS_PER_ROUND = 3
TRACKER_RETRY_WAIT = 10


FADE_STEP_IN = 0.3   
FADE_STEP_OUT = 1.0 / 6.0 


BASE_SCREEN_W = 1920
BASE_SCREEN_H = 1080
OVERLAY_WIDTH_PCT = 45.00             
MIN_HEIGHT_PCT = 7.41                 
HEADER_TOP_PCT = 0.74                 
HEADER_ICON_SIZE_PCT = 2.22           
DIVIDER_Y_PCT = 3.52                  
ROW_START_Y_PCT = 4.26                
ROW_HEIGHT_PCT = 3.40                 
INNER_BOTTOM_PADDING_PCT = 0.0        
RANK_ICON_SIZE_PCT = 3.52             
DIVISION_HEIGHT_PCT = 0.58
DIVISION_GAP_PCT = 0.09
MAX_VISIBLE_PLAYERS = 8

state = {
    "in_match": False,
    "players": [],
    "lock": threading.Lock(),
}

tracker_cache = {}
pixmap_cache = {}

PLAYLIST_IMAGE_MAP = {
    10: "0.png",  # 1v1
    11: "1.png",  # 2v2
    13: "2.png",  # 3v3
    27: "3.png",  # Hoops
    28: "4.png",  # Rumble
    29: "5.png",  # Dropshot
    30: "6.png",  # Snowday
    34: "7.png",  # Tournament
}

# playlist IDs to sum for total matches
ALL_PLAYLIST_IDS = [10, 11, 13, 27, 28, 29, 30, 34, 61, 63]


# img Loading


def get_pixmap(folder: str, filename: str, target_w, target_h) -> QPixmap:
    path = resource_path(os.path.join(folder, filename))
    cache_key = f"{path}_{target_w}x{target_h}"

    if cache_key not in pixmap_cache:
        if os.path.exists(path):
            pm = QPixmap(path)
            if not pm.isNull():
                if target_w is None:
                    scaled = pm.scaledToHeight(
                        target_h, Qt.TransformationMode.SmoothTransformation
                    )
                elif target_h is None:
                    scaled = pm.scaledToWidth(
                        target_w, Qt.TransformationMode.SmoothTransformation
                    )
                else:
                    scaled = pm.scaled(
                        target_w,
                        target_h,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                pixmap_cache[cache_key] = scaled
            else:
                pixmap_cache[cache_key] = None
        else:
            pixmap_cache[cache_key] = None

    return pixmap_cache[cache_key]


# Rank

def is_bot(primary_id: str) -> bool:
    if not primary_id:
        return True
    if "unknown" in primary_id.lower():
        return True
    if "|" not in primary_id:
        return True
    return False

def get_platform_tag(primary_id: str) -> str:
    if is_bot(primary_id):
        return "[BOT]"
    plat = primary_id.split('|')[0].lower()
    mapping = {
        "steam": "[Steam]",
        "epic": "[Epic]",
        "xboxone": "[Xbox]",
        "ps4": "[PSN]",
        "switch": "[Switch]",
    }
    return mapping.get(plat, "[?]")

def get_tier_id(rank_name: str) -> int:
    ranks = [
        "Unranked", "Bronze I", "Bronze II", "Bronze III",
        "Silver I", "Silver II", "Silver III", "Gold I", "Gold II", "Gold III",
        "Platinum I", "Platinum II", "Platinum III", "Diamond I", "Diamond II", "Diamond III",
        "Champion I", "Champion II", "Champion III", "Grand Champion I", "Grand Champion II", "Grand Champion III",
        "Supersonic Legend",
    ]
    try:
        return ranks.index(rank_name)
    except ValueError:
        return 0

def get_div_id(div_name: str) -> int:
    divs = {"Division I": 1, "Division II": 2, "Division III": 3, "Division IV": 4}
    return divs.get(div_name, 0)

def get_div_color_id(tier_id: int) -> int:
    if 1 <= tier_id <= 3: return 1  # Bronze
    elif 4 <= tier_id <= 6: return 2  # Silver
    elif 7 <= tier_id <= 9: return 3  # Gold
    elif 10 <= tier_id <= 12: return 4  # Platinum
    elif 13 <= tier_id <= 15: return 5  # Diamond
    elif 16 <= tier_id <= 18: return 6  # Champion
    elif 19 <= tier_id <= 21: return 7  # GC
    return 7

def shorten_rank(rank_str: str) -> str:
    if not rank_str: return "Unranked"
    s = rank_str.strip()
    if s.lower() == "supersonic legend": return "SSL"
    if s.lower() == "unranked": return "Unranked"
    roman_map = {"I": "1", "II": "2", "III": "3"}
    parts = s.split()
    if len(parts) >= 2:
        num = roman_map.get(parts[-1].upper(), parts[-1])
        if "Grand Champion" in s: return f"GC{num}"
        else: return f"{parts[0][0].upper()}{num}"
    return s

def get_total_matches(stats: dict) -> int:
    total = 0
    for pid in ALL_PLAYLIST_IDS:
        entry = stats.get(pid)
        if entry:
            total += entry.get("matches_played", 0)
    return total


# API tracker.gg 


def player_is_in_current_match(primary_id: str) -> bool:
    with state["lock"]:
        return any(p.get("PrimaryId") == primary_id for p in state["players"])

def should_fetch_stats(cache_entry: dict, now: float) -> bool:
    if not cache_entry: return True
    if cache_entry.get("fetching"): return False
    # Never retry a 404
    if cache_entry.get("not_found"): return False
    age = now - cache_entry.get("timestamp", 0)
    if age > CACHE_TTL: return True
    if cache_entry.get("error") and not cache_entry.get("stats") and age >= TRACKER_RETRY_WAIT: return True
    return False

def request_player_stats_once(slug: str, target_user: str) -> dict:
    url = f"https://api.tracker.gg/api/v2/rocket-league/standard/profile/{slug}/{target_user}"
    response = cf_requests.get(
        url,
        impersonate=random.choice(IMPERSONATE_OPTIONS),
        timeout=8,
    )
    if response.status_code == 404:
        raise ValueError("NOT_FOUND_404")
    response.raise_for_status()
    data = response.json()
    if not isinstance(data.get("data"), dict):
        raise ValueError("Tracker API returned no profile data")
    return data

def parse_tracker_stats(data: dict) -> dict:
    stats = {}
    for seg in data.get("data", {}).get("segments", []):
        if seg.get("type") == "playlist":
            pid = seg.get("attributes", {}).get("playlistId")
            tier = seg.get("stats", {}).get("tier", {}).get("metadata", {}).get("name", "Unranked")
            div_str = seg.get("stats", {}).get("division", {}).get("metadata", {}).get("name", "")
            mmr = seg.get("stats", {}).get("rating", {}).get("value", 0)
            matches_played = seg.get("stats", {}).get("matchesPlayed", {}).get("value", 0) or 0

            stats[pid] = {
                "tier_name": tier,
                "tier_id": get_tier_id(tier),
                "div_name": div_str,
                "div_id": get_div_id(div_str),
                "mmr": int(mmr) if mmr else 0,
                "matches_played": int(matches_played),
            }
    return stats

def fetch_player_stats(primary_id: str, display_name: str):
    if is_bot(primary_id): return
    parts = primary_id.split('|')
    platform = parts[0].lower()
    user_id = parts[1]

    if platform == "switch":
        if DEBUG: print(f"[DEBUG] Skipping Switch player: {display_name}")
        return  
    
    plat_map = {"steam": "steam", "epic": "epic", "xboxone": "xbl", "ps4": "psn", "switch": "switch"}
    slug = plat_map.get(platform, "epic")

    target_user = user_id if slug == "steam" else urllib.parse.quote(display_name, safe="")
    last_error = ""

    while True:
        for attempt in range(TRACKER_ATTEMPTS_PER_ROUND):
            try:
                data = request_player_stats_once(slug, target_user)
                stats = parse_tracker_stats(data)

                tracker_cache[primary_id] = {
                    "timestamp": time.time(),
                    "fetching": False,
                    "error": False,
                    "not_found": False,
                    "stats": stats,
                    "last_error": "",
                    "next_retry": 0,
                }
                return
            except Exception as exc:
                last_error = str(exc)
                if "NOT_FOUND_404" in last_error:
                    if DEBUG: print(f"[DEBUG] 404 for {display_name}, marking not_found, no retries.")
                    tracker_cache[primary_id] = {
                        "timestamp": time.time(),
                        "fetching": False,
                        "error": True,
                        "not_found": True,
                        "stats": tracker_cache.get(primary_id, {}).get("stats", {}),
                        "last_error": last_error,
                        "next_retry": 0,
                    }
                    return
                if DEBUG: print(f"[DEBUG] Tracker API error for {display_name} ({slug}/{target_user}) attempt {attempt + 1}/{TRACKER_ATTEMPTS_PER_ROUND}: {exc}")

        if DEBUG: print(f"[DEBUG] All attempts failed for {display_name}, waiting {TRACKER_RETRY_WAIT}s before retry. Last error: {last_error}")

        old_stats = tracker_cache.get(primary_id, {}).get("stats", {})
        tracker_cache[primary_id] = {
            "timestamp": time.time(),
            "fetching": True,
            "error": True,
            "not_found": False,
            "stats": old_stats,
            "last_error": last_error,
            "next_retry": time.time() + TRACKER_RETRY_WAIT,
        }

        waited = 0.0
        while waited < TRACKER_RETRY_WAIT:
            if not player_is_in_current_match(primary_id):
                if DEBUG: print(f"[DEBUG] {display_name} left match, aborting retry")
                tracker_cache[primary_id] = {
                    "timestamp": time.time(),
                    "fetching": False,
                    "error": True,
                    "not_found": False,
                    "stats": old_stats,
                    "last_error": last_error,
                    "next_retry": 0,
                }
                return
            time.sleep(0.5)
            waited += 0.5


# Socket


def get_rl_window_rect():
    """Returns (left, top, right, bottom) of the RL window, or None if not found."""
    title = config.get("rl_window_title", "Rocket League")
    hwnd = win32gui.FindWindow(None, title)
    if not hwnd:
        result = []
        def enum_cb(h, _):
            t = win32gui.GetWindowText(h)
            if title.lower() in t.lower() and win32gui.IsWindowVisible(h):
                result.append(h)
        win32gui.EnumWindows(enum_cb, None)
        hwnd = result[0] if result else None
    if not hwnd:
        return None
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return left, top, right, bottom
    except Exception:
        return None

def is_cursor_inside_window(hwnd: int) -> bool:
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        x, y = win32gui.GetCursorPos()
        return left <= x <= right and top <= y <= bottom
    except Exception:
        return True

def is_rl_focused() -> bool:
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        if config["rl_window_title"].lower() not in title.lower():
            return False
        if config.get("require_mouse_over_rl_window", False) and not is_cursor_inside_window(hwnd):
            return False
        return True
    except Exception:
        return False

def is_hotkey_pressed() -> bool:
    try:
        if config.get("is_controller", False):
            ctrl_type = config.get("controller_type", "xinput")  # default for old configs
            if ctrl_type == "dualsense":
                # check ps_controller directly 
                if ps_controller is None:
                    return False

                btns = ps_get_inputs()
                mask_id = config.get("controller_button", 0)
                mask = _ps_btns[mask_id]
                return mask in btns
            else:
                xi_state = get_xinput_state()
                if xi_state:
                    btn = config.get("controller_button", 0)
                    return (xi_state.Gamepad.wButtons & btn) == btn
                return False
        else:
            return keyboard.is_pressed(config["hotkey"])
    except Exception as e:
        if DEBUG: print(f"[DEBUG] is_hotkey_pressed exception: {e}")
        return False

def handle(msg: dict):
    evt = msg.get("Event", "")
    data = msg.get("Data", {})
    if isinstance(data, str):
        try: data = json.loads(data)
        except Exception as e:
            if DEBUG: print(f"[DEBUG] Failed to parse event data for '{evt}': {e}")
            return

    with state["lock"]:
        if evt == "UpdateState":
            players = data.get("Players", [])
            state["players"] = []
            now = time.time()
            
            for p in players:
                pid = p.get("PrimaryId", "")
                name = p.get("Name", "?")
                if is_bot(pid): pid = ""
                
                state["players"].append({"Name": name, "TeamNum": p.get("TeamNum", -1), "PrimaryId": pid})
                
                if pid:
                    cache_entry = tracker_cache.get(pid)
                    if should_fetch_stats(cache_entry, now):
                        old_stats = cache_entry.get("stats", {}) if cache_entry else {}
                        tracker_cache[pid] = {
                            "fetching": True,
                            "timestamp": now,
                            "stats": old_stats,
                            "error": False,
                            "not_found": False,
                            "last_error": "",
                            "next_retry": 0,
                        }
                        threading.Thread(target=fetch_player_stats, args=(pid, name), daemon=True).start()
            state["in_match"] = bool(players)

        elif evt in ("MatchCreated", "MatchInitialized", "RoundStarted", "CountdownBegin"):
            state["in_match"] = True
        elif evt in ("MatchEnded", "MatchDestroyed"):
            state["in_match"] = False
            state["players"] = []
            # clear failed/not-found entries
            for pid in list(tracker_cache.keys()):
                if tracker_cache[pid].get("not_found") or tracker_cache[pid].get("error"):
                    del tracker_cache[pid]

def extract_json_objects(buf: bytes):
    objects, i = [], 0
    while i < len(buf):
        if buf[i:i+1] == b"{":
            depth, in_str, escape = 0, False, False
            j = i
            while j < len(buf):
                c = buf[j:j+1]
                if escape: escape = False
                elif c == b"\\": escape = True
                elif c == b'"' and not escape: in_str = not in_str
                elif not in_str:
                    if c == b"{": depth += 1
                    elif c == b"}":
                        depth -= 1
                        if depth == 0:
                            objects.append(buf[i:j+1])
                            i = j + 1
                            break
                j += 1
            else: break
        else: i += 1
    return objects, buf[i:]

def read_stream():
    host, port = config["rl_host"], config["rl_port"]
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((host, port))
            s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n")
            buf = b""
            while True:
                chunk = s.recv(65536)
                if not chunk: break
                buf += chunk
                objects, buf = extract_json_objects(buf)
                for raw in objects:
                    try: handle(json.loads(raw))
                    except Exception as e:
                        if DEBUG: print(f"[DEBUG] Failed to handle socket message: {e}")
                if len(buf) > 1_000_000: buf = b""
            s.close()
        except Exception as e:
            if DEBUG: print(f"[DEBUG] Socket connection error: {e}")
        with state["lock"]:
            state["in_match"] = False
            state["players"] = []
        time.sleep(2)


#  Overlay


class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.screen_geo = self._get_geometry()
        self.metrics = self._build_metrics()
        self.W = self.metrics["overlay_w"]
        self.H = self.metrics["min_h"]
        self.x_pos = self._center_x()

        self._opacity: float = 0.0
        self._fade_target: float = 0.0

        self.setWindowOpacity(0.0)
        self.setGeometry(self.x_pos, self._bottom_y(self.H), self.W, self.H)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._check_visibility)
        self.timer.start(50)

    def _screen_w(self, pct: float) -> int:
        return max(1, round(self.screen_geo.width() * (pct / 100.0)))

    def _screen_h(self, pct: float) -> int:
        return max(1, round(self.screen_geo.height() * (pct / 100.0)))

    def _window_w(self, overlay_w: int, pct: float) -> int:
        return max(1, round(overlay_w * (pct / 100.0)))

    def _build_metrics(self) -> dict:
        overlay_w = self._screen_w(OVERLAY_WIDTH_PCT)
        scale = min(self.screen_geo.width() / BASE_SCREEN_W, self.screen_geo.height() / BASE_SCREEN_H)

        metrics = {
            "overlay_w": overlay_w,
            "min_h": self._screen_h(MIN_HEIGHT_PCT),
            "header_top": self._screen_h(HEADER_TOP_PCT),
            "header_icon": self._screen_h(HEADER_ICON_SIZE_PCT),
            "divider_y": self._screen_h(DIVIDER_Y_PCT),
            "divider_margin_x": self._window_w(overlay_w, 1.79),
            "row_start": self._screen_h(ROW_START_Y_PCT),
            "row_h": self._screen_h(ROW_HEIGHT_PCT),
            "text_baseline": round(self._screen_h(ROW_HEIGHT_PCT) * 0.67),
            "inner_bottom_pad": self._screen_h(INNER_BOTTOM_PADDING_PCT),
            "corner_radius": self._screen_h(0.74),
            "best_col":    self._window_w(overlay_w, 2.3),
            "div1_x":      self._window_w(overlay_w, 16.0),
            "rank_col":    self._window_w(overlay_w, 17.5),
            "div2_x":      self._window_w(overlay_w, 32.0),
            "casual_col":  self._window_w(overlay_w, 33.5),
            "div3_x":      self._window_w(overlay_w, 41.5),
            "matches_col": self._window_w(overlay_w, 43.0),
            "div4_x":      self._window_w(overlay_w, 51.5),
            "name_col":    self._window_w(overlay_w, 53.0),

            "rank_icon": self._screen_h(RANK_ICON_SIZE_PCT),
            "rank_icon_gap": self._window_w(overlay_w, 1.05),
            "rank_fallback_w": self._window_w(overlay_w, 6.00),
            "unranked_text_offset": self._window_w(overlay_w, 6.00),
            "division_h": self._screen_h(DIVISION_HEIGHT_PCT),
            "division_gap": self._screen_h(DIVISION_GAP_PCT),
            "division_next_pad": self._window_w(overlay_w, 1.05),
            "font_size": max(8, round(FONT_SIZE * scale)),
        }
        return metrics

    def _get_geometry(self):
        from PySide6.QtCore import QRect
        rect = get_rl_window_rect()
        if rect:
            l, t, r, b = rect
            return QRect(l, t, r - l, b - t)
        return QApplication.primaryScreen().geometry()

    def _refresh_display_metrics(self):
        current_geo = self._get_geometry()
        if current_geo != self.screen_geo:
            self.screen_geo = current_geo
            self.metrics = self._build_metrics()
            self.W = self.metrics["overlay_w"]
            self.x_pos = self._center_x()

    def _center_x(self) -> int:
        return self.screen_geo.x() + ((self.screen_geo.width() - self.W) // 2)

    def _bottom_y(self, height: int) -> int:
        return self.screen_geo.y() + self.screen_geo.height() - height - 20

    def _check_visibility(self):
        self._refresh_display_metrics()

        with state["lock"]:
            in_match = state["in_match"]
            num_players = len(state["players"])

        visible_players = min(num_players, MAX_VISIBLE_PLAYERS)
        target_h = max(
            self.metrics["min_h"],
            self.metrics["row_start"] + (visible_players * self.metrics["row_h"]) + self.metrics["inner_bottom_pad"],
        )

        if self.width() != self.W or self.height() != target_h:
            self.setGeometry(self.x_pos, self._bottom_y(target_h), self.W, target_h)

        should_show = is_rl_focused() and is_hotkey_pressed() and in_match and num_players > 0
        
        if should_show:
            self._fade_target = 1.0
            if self._opacity < self._fade_target:
                self._opacity = min(1.0, self._opacity + FADE_STEP_IN)
        else:
            self._fade_target = 0.0
            if self._opacity > self._fade_target:
                self._opacity = max(0.0, self._opacity - FADE_STEP_OUT)

        if self._opacity > 0.0 and not self.isVisible():
            self.show()

        self.setWindowOpacity(self._opacity)

        if self._opacity <= 0.0 and self.isVisible():
            self.hide()

        if self._opacity > 0.0:
            self.update()


    def division_stack_height(self, tier_id: int) -> int:
        if tier_id >= 22 or tier_id <= 0: return 0
        color_id = get_div_color_id(tier_id)
        pm_filled = get_pixmap("Divisions", f"{color_id}.png", None, self.metrics["division_h"])
        pm_blank  = get_pixmap("Divisions", "0.png",            None, self.metrics["division_h"])
        sample_h = max(
            pm_filled.height() if pm_filled else self.metrics["division_h"],
            pm_blank.height()  if pm_blank  else self.metrics["division_h"],
        )
        return (sample_h * 4) + (self.metrics["division_gap"] * 3)

    def draw_stacked_divisions(self, painter, x, y, tier_id, div_level):
        if div_level <= 0 or tier_id >= 22 or tier_id <= 0: return 0
        color_id = get_div_color_id(tier_id)
        pm_filled = get_pixmap("Divisions", f"{color_id}.png", None, self.metrics["division_h"])
        pm_blank  = get_pixmap("Divisions", "0.png",            None, self.metrics["division_h"])

        if not pm_filled or not pm_blank:
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(x, y + self.metrics["text_baseline"], f"D{div_level}")
            return self.metrics["division_h"] * 4 + self.metrics["division_next_pad"]

        current_y = y
        max_w = 0
        for i in range(4, 0, -1):
            pm = pm_filled if i <= div_level else pm_blank
            painter.drawPixmap(x, current_y, pm)
            current_y += pm.height() + self.metrics["division_gap"]
            max_w = max(max_w, pm.width())

        return max_w + self.metrics["division_next_pad"]


    def paintEvent(self, event):
        with state["lock"]:
            players = sorted(list(state["players"]), key=lambda p: p.get("TeamNum", -1), reverse=True)[:MAX_VISIBLE_PLAYERS]

        team_counts = {0: 0, 1: 0}
        for p in players:
            t = p.get("TeamNum")
            if t in team_counts: team_counts[t] += 1

        max_t = max(team_counts.values()) if team_counts else 0
        if max_t <= 1: playlist_id = 10
        elif max_t == 2: playlist_id = 11
        elif max_t >= 3: playlist_id = 13
        else: playlist_id = -1

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Background
        painter.setBrush(QColor(17, 24, 39, 216))
        painter.setPen(QPen(QColor(45, 55, 72), 1))
        painter.drawRoundedRect(
            0, 0,
            self.width() - 1, self.height() - 1,
            self.metrics["corner_radius"], self.metrics["corner_radius"],
        )

        # Column labels
        header_text_y = self.metrics["divider_y"] // 2 + self.metrics["font_size"] // 2
        label_font = QFont(FONT_NAME, max(7, self.metrics["font_size"] - 1))
        painter.setFont(label_font)
        painter.setPen(QColor(100, 116, 139))
        painter.drawText(self.metrics["best_col"], header_text_y, "Best Rank")

        ranked_text = "Ranked "
        painter.drawText(self.metrics["rank_col"], header_text_y, ranked_text)
        
        pl_target = int(self.metrics["header_icon"] * 0.8)
        pl_pm = get_pixmap("Playlists", PLAYLIST_IMAGE_MAP.get(playlist_id, "0.png"), pl_target, pl_target)
        if pl_pm:
            fm = painter.fontMetrics()
            ranked_text_w = fm.horizontalAdvance(ranked_text)
            px = self.metrics["rank_col"] + ranked_text_w
            py = self.metrics["divider_y"] // 2 - pl_pm.height() // 2
            painter.drawPixmap(px, py, pl_pm)

        painter.drawText(self.metrics["casual_col"], header_text_y, "Casual")
        painter.drawText(self.metrics["matches_col"], header_text_y, "Matches")

        # horizontal divider
        painter.setPen(QPen(QColor(45, 55, 72), 1))
        painter.drawLine(
            self.metrics["divider_margin_x"], self.metrics["divider_y"],
            self.width() - self.metrics["divider_margin_x"], self.metrics["divider_y"],
        )

        # vertical dividers
        div_top = self.metrics["divider_y"]
        div_bottom = self.height()
        painter.drawLine(self.metrics["div1_x"], div_top, self.metrics["div1_x"], div_bottom)
        painter.drawLine(self.metrics["div2_x"], div_top, self.metrics["div2_x"], div_bottom)
        painter.drawLine(self.metrics["div3_x"], div_top, self.metrics["div3_x"], div_bottom)
        painter.drawLine(self.metrics["div4_x"], div_top, self.metrics["div4_x"], div_bottom)

        col_best    = self.metrics["best_col"]
        col_ranked  = self.metrics["rank_col"]
        col_casual  = self.metrics["casual_col"]
        col_matches = self.metrics["matches_col"]
        col_name    = self.metrics["name_col"]
        font_regular = QFont(FONT_NAME, self.metrics["font_size"])

        for i, p in enumerate(players):
            y = self.metrics["row_start"] + (i * self.metrics["row_h"])
            if y + self.metrics["row_h"] > self.height(): break

            text_y = y + self.metrics["text_baseline"]
            team = p.get("TeamNum", -1)
            color = QColor(255, 160, 64) if team == 1 else QColor(79, 195, 247)
            pid = p.get("PrimaryId", "")
            platform_tag = get_platform_tag(pid)

            painter.setFont(font_regular)

            cache_entry = tracker_cache.get(pid, {})
            stats = cache_entry.get("stats", {})

            if not pid:
                pass
            elif cache_entry.get("fetching") and not stats:
                painter.setPen(QColor(209, 213, 219))
                painter.drawText(col_ranked, text_y, "Loading...")
            elif cache_entry.get("error") and not stats:
                painter.setPen(QColor(209, 213, 219))
                painter.drawText(col_ranked, text_y, "API Error")
            elif stats:
                painter.setPen(QColor(209, 213, 219))
                
                # best rank
                best_playlist = None
                best_tier = -1
                best_div = -1
                
                for p_id in [10, 11, 13]:
                    rnk = stats.get(p_id)
                    if rnk:
                        t = rnk["tier_id"]
                        d = rnk["div_id"]
                        if t > best_tier or (t == best_tier and d > best_div):
                            best_tier = t
                            best_div = d
                            best_playlist = p_id
                            
                if best_tier >= 0:
                    bx = col_best

                    m_size = int(self.metrics["rank_icon"] * 0.75)
                    if best_playlist is not None:
                        m_pm = get_pixmap("Playlists", PLAYLIST_IMAGE_MAP.get(best_playlist, "0.png"), m_size, m_size)
                        if m_pm:
                            icon_y = y + ((self.metrics["row_h"] - m_pm.height()) // 2)
                            painter.drawPixmap(bx, icon_y, m_pm)
                        bx += m_size + self.metrics["rank_icon_gap"]
                        
                    t_size = self.metrics["rank_icon"]
                    t_pm = get_pixmap("Tiers", f"{best_tier}.png", t_size, t_size)
                    if t_pm:
                        icon_y = y + ((self.metrics["row_h"] - t_pm.height()) // 2)
                        painter.drawPixmap(bx, icon_y, t_pm)
                        bx += t_size + self.metrics["rank_icon_gap"]
                    else:
                        painter.drawText(bx, text_y, "Unranked")
                        bx += self.metrics["rank_fallback_w"]
                        
                    div_stack_h = self.division_stack_height(best_tier)
                    div_y = y + ((self.metrics["row_h"] - div_stack_h) // 2)
                    bx += self.draw_stacked_divisions(painter, bx, div_y, best_tier, best_div)

                # current playlist ranked
                rnk_data = stats.get(playlist_id)
                if rnk_data:
                    tier_id = rnk_data["tier_id"]
                    div_id  = rnk_data["div_id"]
                    mmr     = rnk_data["mmr"]

                    rx = col_ranked
                    t_size = self.metrics["rank_icon"]
                    t_pm = get_pixmap("Tiers", f"{tier_id}.png", t_size, t_size)
                    if t_pm:
                        icon_y = y + ((self.metrics["row_h"] - t_pm.height()) // 2)
                        painter.drawPixmap(rx + ((t_size - t_pm.width()) // 2), icon_y, t_pm)
                        rx += t_size + self.metrics["rank_icon_gap"]
                    else:
                        painter.drawText(rx, text_y, shorten_rank(rnk_data["tier_name"]))
                        rx += self.metrics["rank_fallback_w"]

                    div_stack_h = self.division_stack_height(tier_id)
                    div_y = y + ((self.metrics["row_h"] - div_stack_h) // 2)
                    rx += self.draw_stacked_divisions(painter, rx, div_y, tier_id, div_id)
                    painter.drawText(rx, text_y, f"{mmr}")
                else:
                    t_size = self.metrics["rank_icon"]
                    t_pm = get_pixmap("Tiers", "0.png", t_size, t_size)
                    if t_pm:
                        icon_y = y + ((self.metrics["row_h"] - t_pm.height()) // 2)
                        painter.drawPixmap(col_ranked + ((t_size - t_pm.width()) // 2), icon_y, t_pm)
                        painter.drawText(col_ranked + self.metrics["unranked_text_offset"], text_y, "Unranked")

                # casual MMR
                painter.setPen(QColor(160, 160, 160))
                cas_data = stats.get(0)
                if cas_data:
                    painter.drawText(col_casual, text_y, f"{cas_data['mmr']}")

                # total matches across all playlists
                total_matches = get_total_matches(stats)
                if total_matches > 0:
                    painter.setPen(QColor(160, 160, 160))
                    painter.drawText(col_matches, text_y, f"{total_matches:,}")

            painter.setPen(color)
            painter.drawText(col_name, text_y, f"{platform_tag} {p['Name']}")



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-r", "--rebind", action="store_true", help="Force the hotkey rebind window to appear.")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable verbose debug logging.")
    args, unknown = parser.parse_known_args()

    if args.debug:
        DEBUG = True
        print("[Debug] Debug mode enabled.")

    app = QApplication(sys.argv)
    
    # Needs to be called after QApplication so QDialog can be used
    load_or_setup_config(force_rebind=args.rebind)
    check_for_updates()
    
    threading.Thread(target=read_stream, daemon=True).start()

    if config.get("is_controller"):
        ctrl_type = config.get("controller_type", "xinput")
        if ctrl_type == "dualsense":
            bind_msg = f"[DualSense Controller Button {_ps_btns[config.get('controller_button')]}]"
        else:
            bind_msg = f"[Controller Button {config.get('controller_button')}]"
    else:
        bind_msg = f"[{config['hotkey'].upper()}]"

    print(f"[Overlay] Launch Rocket League and hold {bind_msg} to view stats.", flush=True)

    overlay = Overlay()
    sys.exit(app.exec())