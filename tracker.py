import json
import time
import urllib.error
import urllib.parse
import urllib.request

from utils import (
    DEBUG, state, tracker_cache,
    TRACKER_ATTEMPTS_PER_ROUND, TRACKER_RETRY_WAIT, CACHE_TTL,
    is_bot, get_div_id, log,
)

RLAPI_BASE = "https://rlapi-serve.nixvio64.workers.dev"
REQUEST_TIMEOUT = 8


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

def request_player_stats_once(primary_id: str, display_name: str) -> dict:
    path = urllib.parse.quote(primary_id, safe="")
    url = f"{RLAPI_BASE}/player/{path}/ranks"
    if display_name:
        url += "?name=" + urllib.parse.quote(display_name, safe="")

    req = urllib.request.Request(url, headers={"User-Agent": "InGameRank"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 404):
            raise ValueError("NOT_FOUND_404")
        body = exc.read().decode("utf-8", "replace")[:200]
        raise ValueError(f"HTTP {exc.code}: {body}")


def parse_player_stats(data: dict) -> dict:
    stats = {}
    for pid, rank in (data.get("ranks") or {}).items():
        div_name = rank.get("division_name", "")
        stats[int(pid)] = {
            "tier_name": rank.get("tier_name", "Unranked"),
            "tier_id": int(rank.get("tier_id", 0) or 0),
            "div_name": div_name,
            "div_id": get_div_id(div_name),
            "mmr": int(rank.get("mmr", 0) or 0),
            "matches_played": int(rank.get("matches_played", 0) or 0),
        }
    return stats


def fetch_player_stats(primary_id: str, display_name: str):
    if is_bot(primary_id):
        return

    last_error = ""

    while True:
        for attempt in range(TRACKER_ATTEMPTS_PER_ROUND):
            try:
                data = request_player_stats_once(primary_id, display_name)
                stats = parse_player_stats(data)

                tracker_cache[primary_id] = {
                    "timestamp": time.time(),
                    "fetching": False,
                    "error": False,
                    "not_found": False,
                    "stats": stats,
                    "avatar_url": data.get("avatar_url"),
                    "display_name": data.get("display_name") or display_name,
                    "last_error": "",
                    "next_retry": 0,
                }
                return
            except Exception as exc:
                last_error = str(exc)
                if "NOT_FOUND_404" in last_error:
                    log(f"No data for {display_name}, marking not_found, no retries.", "DEBUG")
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
                    log(f"RLAPI error for {display_name} ({primary_id}) attempt {attempt + 1}/{TRACKER_ATTEMPTS_PER_ROUND}: {exc}", "DEBUG")

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
