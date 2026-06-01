# early init must run before anything imports dualsense_controller
import sys
import warnings
warnings.filterwarnings("ignore", message="Microphone state initially", category=UserWarning)
import logging

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


# imports (safe now stderr/logging filters are in place)
import threading
import urllib.request
import argparse
import webbrowser
import re
import ctypes

from PySide6.QtWidgets import (QApplication, QDialog, QLabel, QVBoxLayout,
                                QHBoxLayout, QPushButton, QWidget, QMenuBar)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon

import utils
from config import config, load_or_setup_config
import controllers
from stream import read_stream
from overlay import Overlay


# update checker

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


def check_for_updates(silent=False):
    try:
        url = "https://github.com/nixvio64/InGameRank/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=5) as response:
            final_url = response.geturl()

        if "/releases/tag/" in final_url:
            latest_version = final_url.split("/releases/tag/")[-1]

            def parse_v(v):
                return tuple(int(i) for i in re.findall(r'\d+', v))

            if parse_v(latest_version) > parse_v(utils.VERSION):
                dialog = UpdateDialog(latest_version, final_url)
                dialog.exec()
            elif not silent:
                _no_update_dialog()
    except Exception as e:
        utils.log(f"Update check failed: {e}", "DEBUG")
        if not silent:
            _no_update_dialog()


def _no_update_dialog():
    dlg = QDialog()
    dlg.setWindowTitle("Up to Date")
    dlg.setFixedSize(220, 80)
    dlg.setWindowFlags(Qt.WindowType.Dialog)
    layout = QVBoxLayout()
    label = QLabel("No update available.")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet("font-size: 12px; font-family: Segoe UI;")
    layout.addWidget(label)
    dlg.setLayout(layout)
    dlg.exec()


# log window

class _TeeOutput:
    """Writes to multiple streams at once."""
    def __init__(self, *outputs):
        self.outputs = outputs

    def write(self, data):
        for out in self.outputs:
            out.write(data)
        return len(data) if isinstance(data, str) else 0

    def flush(self):
        for out in self.outputs:
            out.flush()


class LogWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("InGameRank")
        self.setWindowIcon(QIcon(utils.resource_path("InGameRank.ico")))
        self.setFixedSize(340, 60)

        self._buffer = ""
        self._console = None

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(2)

        # menu bar
        menu_bar = QMenuBar(self)
        menu_bar.setStyleSheet("QMenuBar { padding: 0px 2px; } QMenuBar::item { padding: 4px 12px; }")
        file_menu = menu_bar.addMenu("File")

        self._debug_action = QAction("Debug", self)
        self._debug_action.setCheckable(True)
        self._debug_action.setChecked(utils.DEBUG)
        self._debug_action.toggled.connect(self._on_debug_toggle)
        file_menu.addAction(self._debug_action)

        file_menu.addSeparator()

        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        update_action = QAction("Check for Updates", self)
        update_action.triggered.connect(check_for_updates)
        file_menu.addAction(update_action)

        file_menu.addSeparator()

        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        file_menu.addAction(about_action)

        layout.addWidget(menu_bar)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("font-size: 12px; font-family: Segoe UI;")
        self.label.setWordWrap(True)
        self.label.setMinimumHeight(36)
        layout.addWidget(self.label)
        self.setLayout(layout)

        if utils.DEBUG:
            self._on_debug_toggle(True)

    def write(self, data):
        if isinstance(data, str):
            self._buffer += data
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    self.label.setText(line.strip())
        return len(data) if isinstance(data, str) else 0

    def flush(self):
        pass

    def closeEvent(self, event):
        QApplication.quit()
        super().closeEvent(event)

    def _on_debug_toggle(self, enabled):
        utils.DEBUG = enabled
        if enabled:
            ctypes.windll.kernel32.AllocConsole()
            self._console = open("CONOUT$", "w")
            sys.stdout = _TeeOutput(self, self._console)
            utils.log("Debug mode enabled.", "DEBUG")
        else:
            sys.stdout = self
            if self._console:
                self._console.close()
                self._console = None
            try:
                ctypes.windll.kernel32.FreeConsole()
            except Exception:
                pass

    def _open_settings(self):
        from config import SettingsDialog
        dlg = SettingsDialog()
        dlg.exec()

    def _show_about(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("About")
        dlg.setFixedSize(280, 100)
        dlg.setWindowFlags(Qt.WindowType.Dialog)
        layout = QVBoxLayout()
        label = QLabel(f"InGameRank {utils.VERSION}\n\nMade by nixvio64")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-size: 13px; font-family: Segoe UI;")
        layout.addWidget(label)
        dlg.setLayout(layout)
        dlg.exec()


# entry point

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-r", "--rebind", action="store_true", help="Force the hotkey rebind window to appear.")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable verbose debug logging.")
    args, unknown = parser.parse_known_args()

    if args.debug:
        utils.DEBUG = True

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(utils.resource_path("InGameRank.ico")))

    log_window = LogWindow()
    log_window.show()
    if not utils.DEBUG:
        sys.stdout = log_window

    load_or_setup_config(force_rebind=args.rebind)
    check_for_updates(silent=True)

    threading.Thread(target=read_stream, daemon=True).start()

    if config.get("is_controller"):
        ctrl_type = config.get("controller_type", "xinput")
        btn_disp = controllers.get_button_display(ctrl_type, config.get("controller_button"))
        bind_msg = f"Hold {btn_disp}"
    else:
        bind_msg = f"Hold [{config['hotkey'].upper()}]"

    utils.log(f"Launch Rocket League and {bind_msg} to view stats.", "Overlay")

    overlay = Overlay()
    sys.exit(app.exec())
