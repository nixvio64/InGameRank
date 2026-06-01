import time
import urllib.request
import urllib.parse
import threading
import random

from curl_cffi import requests as cf_requests

from utils import (
    DEBUG, IMPERSONATE_OPTIONS, state, tracker_cache,
    TRACKER_ATTEMPTS_PER_ROUND, TRACKER_RETRY_WAIT, CACHE_TTL,
    is_bot, get_tier_id, get_div_id, log,
)


# cache helpers

def player_is_in_current_match(primary_id: str) -> bool:
    with state["lock"]:
        return any(p.get("PrimaryId") == primary_id for p in state["players"])


def should_fetch_stats(cache_entry: dict, now: float) -> bool:
    if not cache_entry:
        return True
    if cache_entry.get("fetching"):
        return False
    if cache_entry.get("not_found"):
        return False
    age = now - cache_entry.get("timestamp", 0)
    if age > CACHE_TTL:
        return True
    if cache_entry.get("error") and not cache_entry.get("stats") and age >= TRACKER_RETRY_WAIT:
        return True
    return False


# API

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
    if is_bot(primary_id):
        return
    parts = primary_id.split("|")
    platform = parts[0].lower()
    user_id = parts[1]

    if platform == "switch":
        log(f"Skipping Switch player: {display_name}", "DEBUG")
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
                    log(f"404 for {display_name}, marking not_found, no retries.", "DEBUG")
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
                if DEBUG:
                    log(f"Tracker API error for {display_name} ({slug}/{target_user}) attempt {attempt + 1}/{TRACKER_ATTEMPTS_PER_ROUND}: {exc}", "DEBUG")

        log(f"All attempts failed for {display_name}, waiting {TRACKER_RETRY_WAIT}s before retry. Last error: {last_error}", "DEBUG")

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
                log(f"{display_name} left match, aborting retry", "DEBUG")
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
