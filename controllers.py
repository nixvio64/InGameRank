import ctypes
import threading
import time
import warnings
import atexit
import os
import signal
from ctypes import wintypes

from dualsense_controller import DualSenseController
import win32gui
import keyboard

from utils import DEBUG, log
from config import config

# XInput

class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", wintypes.BYTE),
        ("bRightTrigger", wintypes.BYTE),
        ("sThumbLX", wintypes.SHORT),
        ("sThumbLY", wintypes.SHORT),
        ("sThumbRX", wintypes.SHORT),
        ("sThumbRY", wintypes.SHORT),
    ]

class XINPUT_STATE(ctypes.Structure):
    _fields_ = [
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
    if not xinput:
        return None
    state = XINPUT_STATE()
    if xinput.XInputGetState(user_index, ctypes.byref(state)) == 0:
        return state
    return None


# DualSense

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
        log("Disconnected. Waiting for reconnect...", "Controller")

    threading.Thread(target=_reconnect_loop, daemon=True).start()


def is_ps_connected() -> bool:
    device_infos = DualSenseController.enumerate_devices()
    if len(device_infos) < 1:
        return False
    return True


def setup_ps_controller() -> None | DualSenseController:
    if not is_ps_connected():
        log("setup_ps_controller: No DualSense detected", "DEBUG")
        return None

    controller = DualSenseController()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        controller.activate()

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
                log("Reconnected!", "Controller")
                log("DualSense connected by monitor thread", "DEBUG")


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
    _cleanup()
    os._exit(0)


signal.signal(signal.SIGINT, _sigint_handler)


# RL window helpers

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


# hotkey check

def is_hotkey_pressed() -> bool:
    try:
        if config.get("is_controller", False):
            ctrl_type = config.get("controller_type", "xinput")
            if ctrl_type == "dualsense":
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
        log(f"is_hotkey_pressed exception: {e}", "DEBUG")
        return False


# button display names

XINPUT_BUTTON_DISPLAY = {
    0x0001: "D-Pad Up",    0x0002: "D-Pad Down",
    0x0004: "D-Pad Left",  0x0008: "D-Pad Right",
    0x0010: "Start",       0x0020: "Select",
    0x0040: "L-Stick",     0x0080: "R-Stick",
    0x0100: "LB",          0x0200: "RB",
    0x1000: "A",           0x2000: "B",
    0x4000: "X",           0x8000: "Y",
}

DS4_BTN_DISPLAY = {
    "btn_cross":    "Cross",
    "btn_circle":   "Circle",
    "btn_square":   "Square",
    "btn_triangle": "Triangle",
    "btn_l1": "L1",              "btn_r1": "R1",
    "btn_l2": "L2",              "btn_r2": "R2",
    "btn_l3": "L3",              "btn_r3": "R3",
    "btn_options": "Options",    "btn_create": "Create",
    "btn_ps": "PS",              "btn_touchpad": "Touchpad",
    "btn_mute": "Mute",
    "btn_up": "D-Pad Up",        "btn_down": "D-Pad Down",
    "btn_left": "D-Pad Left",    "btn_right": "D-Pad Right",
}


def get_button_display(controller_type: str, raw_button) -> str:
    """Convert a raw controller button id to a human-readable name."""
    if controller_type == "dualsense":
        if isinstance(raw_button, int):
            raw_button = _ps_btns[raw_button] if 0 <= raw_button < len(_ps_btns) else "?"
        return DS4_BTN_DISPLAY.get(raw_button, raw_button)
    else:
        btn = raw_button if isinstance(raw_button, int) else raw_button
        return XINPUT_BUTTON_DISPLAY.get(btn, f"Btn {btn}")
