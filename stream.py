import socket
import json
import threading
import time

from utils import state, tracker_cache, DEBUG, is_bot, log
from config import config
from tracker import should_fetch_stats, fetch_player_stats


# message handling

def handle(msg: dict):
    evt = msg.get("Event", "")
    data = msg.get("Data", {})
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as e:
            log(f"Failed to parse event data for '{evt}': {e}", "DEBUG")
            return

    with state["lock"]:
        if evt == "UpdateState":
            players = data.get("Players", [])
            state["players"] = []
            now = time.time()

            for p in players:
                pid = p.get("PrimaryId", "")
                name = p.get("Name", "?")
                if is_bot(pid):
                    pid = ""

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
            for pid in list(tracker_cache.keys()):
                if tracker_cache[pid].get("not_found") or tracker_cache[pid].get("error"):
                    del tracker_cache[pid]


# stream reader

def extract_json_objects(buf: bytes):
    objects, i = [], 0
    while i < len(buf):
        if buf[i:i+1] == b"{":
            depth, in_str, escape = 0, False, False
            j = i
            while j < len(buf):
                c = buf[j:j+1]
                if escape:
                    escape = False
                elif c == b"\\":
                    escape = True
                elif c == b'"' and not escape:
                    in_str = not in_str
                elif not in_str:
                    if c == b"{":
                        depth += 1
                    elif c == b"}":
                        depth -= 1
                        if depth == 0:
                            objects.append(buf[i:j+1])
                            i = j + 1
                            break
                j += 1
            else:
                break
        else:
            i += 1
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
                if not chunk:
                    break
                buf += chunk
                objects, buf = extract_json_objects(buf)
                for raw in objects:
                    try:
                        handle(json.loads(raw))
                    except Exception as e:
                        log(f"Failed to handle socket message: {e}", "DEBUG")
                if len(buf) > 1_000_000:
                    buf = b""
            s.close()
        except Exception as e:
            log(f"Socket connection error: {e}", "DEBUG")
        with state["lock"]:
            state["in_match"] = False
            state["players"] = []
        time.sleep(2)
