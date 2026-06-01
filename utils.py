import sys
import os
import threading

from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

# shared globals

VERSION = "v1.0.7"
DEBUG = False

IMPERSONATE_OPTIONS = [
    "chrome120", "chrome124",
    "edge99", "edge101",
]

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
    10: "0.png",
    11: "1.png",
    13: "2.png",
    27: "3.png",
    28: "4.png",
    29: "5.png",
    30: "6.png",
    34: "7.png",
}

ALL_PLAYLIST_IDS = [10, 11, 13, 27, 28, 29, 30, 34, 61, 63]


# path / pixmap helpers

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


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


# rank helpers

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
    plat = primary_id.split("|")[0].lower()
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
    if 1 <= tier_id <= 3:
        return 1
    elif 4 <= tier_id <= 6:
        return 2
    elif 7 <= tier_id <= 9:
        return 3
    elif 10 <= tier_id <= 12:
        return 4
    elif 13 <= tier_id <= 15:
        return 5
    elif 16 <= tier_id <= 18:
        return 6
    elif 19 <= tier_id <= 21:
        return 7
    return 7


def shorten_rank(rank_str: str) -> str:
    if not rank_str:
        return "Unranked"
    s = rank_str.strip()
    if s.lower() == "supersonic legend":
        return "SSL"
    if s.lower() == "unranked":
        return "Unranked"
    roman_map = {"I": "1", "II": "2", "III": "3"}
    parts = s.split()
    if len(parts) >= 2:
        num = roman_map.get(parts[-1].upper(), parts[-1])
        if "Grand Champion" in s:
            return f"GC{num}"
        else:
            return f"{parts[0][0].upper()}{num}"
    return s


def get_total_matches(stats: dict) -> int:
    total = 0
    for pid in ALL_PLAYLIST_IDS:
        entry = stats.get(pid)
        if entry:
            total += entry.get("matches_played", 0)
    return total


# logging

def log(msg: str, tag: str = None):
    """Print to stdout.  [tag] prefix only shown when DEBUG is on.  tag=\"DEBUG\" messages are silent unless DEBUG."""
    if tag == "DEBUG" and not DEBUG:
        return
    if tag and DEBUG:
        print(f"[{tag}] {msg}")
    else:
        print(msg)
