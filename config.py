import os
import json
import time
import threading

from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QHBoxLayout, QPushButton
from PySide6.QtCore import Qt, QThread, Signal
import keyboard

from utils import DEBUG, log

# config

CONFIG_FILE = "config.json"
config = {
    "hotkey": "tab",
    "is_controller": False,
    "controller_type": "xinput",
    "controller_button": 0,
    "joy_id": 0,
    "rl_window_title": "Rocket League",
    "rl_host": "127.0.0.1",
    "rl_port": 49123,
    "require_mouse_over_rl_window": True,
}


# dialogs

class BindWorker(QThread):
    finished_bind = Signal(str, bool, int, int, str)

    def run(self):
        time.sleep(0.5)

        # late imports controllers needs config, so we import here
        import controllers
        from controllers import setup_ps_controller, get_xinput_state, ps_get_inputs, _ps_btns

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

            # XInput
            xi = get_xinput_state()
            if xi and xi.Gamepad.wButtons != 0:
                btn = xi.Gamepad.wButtons
                while True:
                    xi2 = get_xinput_state()
                    if not xi2 or xi2.Gamepad.wButtons == 0:
                        break
                    time.sleep(0.05)
                self.finished_bind.emit("", True, btn, 0, "xinput")
                break

            # DualSense
            found_ds = False
            if controllers.ps_controller:
                ps_input_result = ps_get_inputs()
                if ps_input_result:
                    ps_btn = ps_input_result[0]
                    self.finished_bind.emit("", True, _ps_btns.index(ps_btn), 0, "dualsense")
                    found_ds = True

            if found_ds:
                break

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

        from controllers import _ps_btns, get_button_display

        if is_controller:
            config["is_controller"] = True
            config["controller_type"] = controller_type
            config["controller_button"] = btn
            config["joy_id"] = joy_id
            btn_display = get_button_display(controller_type, btn)
            log(f"Bound to {btn_display}", "Overlay")
        else:
            config["is_controller"] = False
            config["hotkey"] = key
            log(f"Bound to keyboard key: {key.upper()}", "Overlay")

        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
        log("Saved to config.json!", "Overlay")
        self.accept()


# config loader

def load_or_setup_config(force_rebind=False):
    global config

    # late imports controllers needs config at module level, so do this here
    from controllers import setup_ps_controller, _ps_monitor_thread

    needs_setup = force_rebind
    is_first_run = False

    if os.path.exists(CONFIG_FILE) and not force_rebind:
        try:
            with open(CONFIG_FILE, "r") as f:
                config.update(json.load(f))
            config.setdefault("controller_type", "xinput")
            config.setdefault("joy_id", 0)
            if config.get("controller_type") == "dualsense":
                setup_ps_controller()
        except Exception as e:
            log(f"Failed to load config: {e}", "Overlay")
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

    if config.get("is_controller") and config.get("controller_type") == "dualsense":
        threading.Thread(target=_ps_monitor_thread, daemon=True).start()


# settings dialog

class SettingsDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Settings")
        self.setFixedSize(300, 120)
        self.setWindowFlags(Qt.WindowType.Dialog)

        layout = QVBoxLayout()

        from PySide6.QtWidgets import QSpinBox

        # current binding + rebind button
        bind_layout = QHBoxLayout()
        bind_str = self._current_bind_str()
        self._bind_label = QLabel(f"Current binding: {bind_str}")
        self._bind_label.setStyleSheet("font-size: 12px; font-family: Segoe UI;")
        bind_layout.addWidget(self._bind_label)
        bind_layout.addStretch()
        rebind_btn = QPushButton("Rebind…")
        rebind_btn.setFixedSize(70, 24)
        rebind_btn.clicked.connect(self._on_rebind)
        bind_layout.addWidget(rebind_btn)
        layout.addLayout(bind_layout)

        # port
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("Port:"))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(config.get("rl_port", 49123))
        port_layout.addWidget(self.port_spin)
        layout.addLayout(port_layout)

        # save / cancel
        btn_layout = QHBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        save_btn = QPushButton("Save")
        save_btn.setFixedSize(80, 28)
        save_btn.clicked.connect(self._on_save)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(80, 28)
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _current_bind_str(self) -> str:
        if config.get("is_controller"):
            ctrl_type = config.get("controller_type", "xinput")
            btn = config.get("controller_button", 0)
            # late import
            from controllers import get_button_display
            return get_button_display(ctrl_type, btn)
        else:
            return config.get("hotkey", "tab").upper()

    def _on_save(self):
        global config
        config["rl_port"] = self.port_spin.value()
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
        log("Settings saved.", "Overlay")
        self.accept()

    def _on_rebind(self):
        dialog = SetupDialog()
        dialog.exec()
        self._bind_label.setText(f"Current binding: {self._current_bind_str()}")
