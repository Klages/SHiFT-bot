import html
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone

import feedparser
import requests
import waitress
from flask import Flask, jsonify, request, send_from_directory

# ---------------- CONFIG ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
STORAGE_FILE = os.path.join(DATA_DIR, "shift_codes_state.json")
LEGACY_STORAGE_FILE = "shift_codes_state.json"  # old location, imported once if found

CHECK_INTERVAL = max(5, int(os.environ.get("CHECK_INTERVAL_MINUTES", "120"))) * 60
NOTIFY_MAX_AGE_DAYS = 3
ALERT_AFTER_FAILURES = 3  # consecutive failed checks before a Discord outage alert
DISCORD_DELAY = 1
MANUAL_CHECK_COOLDOWN = 120  # Reddit allows very few unauthenticated requests per window

# The RSS feed is served to unauthenticated clients that identify themselves honestly.
# The .json endpoint is blocked (403) and browser-like User-Agents get rate limited (429).
REDDIT_URL = "https://www.reddit.com/r/BorderlandsShiftCodes/new/.rss?limit=50"
USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT",
    "windows:bl4-shift-tracker:2.0 (self-hosted personal tool)",
)

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))

BL4_RELEASE_UTC = 1757702400  # Sep 12, 2025 UTC
# ----------------------------------------

app = Flask(__name__, static_folder=None)
app.json.sort_keys = False  # keep the newest-first order in JSON responses

# ---------------- GLOBAL STATE & LOGGING ----------------
state_lock = threading.Lock()
wake_event = threading.Event()
last_checked_at = None
last_error = None
last_manual_check = 0.0
LOGS = []
MAX_LOGS = 100
log_lock = threading.Lock()


def log(message):
    full_msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    try:
        print(full_msg, flush=True)
    except UnicodeEncodeError:  # e.g. a Windows cp1252 console can't show emoji
        print(full_msg.encode("ascii", "replace").decode("ascii"), flush=True)
    with log_lock:
        LOGS.insert(0, full_msg)
        del LOGS[MAX_LOGS:]


# ---------------- STORAGE ----------------
def _parse_iso(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _migrate_entry(entry):
    """Older versions stored 'found' as a display string only; add a sortable timestamp."""
    if not entry.get("found_at"):
        try:
            naive = datetime.strptime(entry.get("found", ""), "%b %d, %Y, %H:%M")
            entry["found_at"] = naive.astimezone(timezone.utc).isoformat()
        except ValueError:
            entry["found_at"] = datetime.fromtimestamp(0, timezone.utc).isoformat()
    entry.pop("found", None)
    return entry


def load_state():
    for path in (STORAGE_FILE, LEGACY_STORAGE_FILE):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {code: _migrate_entry(e) for code, e in data.items()}
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, OSError, AttributeError) as e:
            log(f"⚠️ Could not read {path}: {e}")
    return {}


def save_state(codes):
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(codes, f, indent=2)
        os.replace(tmp, STORAGE_FILE)  # atomic: a crash can't leave a half-written file
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ---------------- PARSING ----------------
CODE_PATTERN = re.compile(r"(?<![A-Z0-9-])[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}(?![A-Z0-9-])", re.IGNORECASE)

BL4_RE = re.compile(
    r"\bbl\s?4\b|\bborderlands\s*4\b|\bborderlands\s*1\W+2\W+3\W+(?:and\s+)?4\b",
    re.IGNORECASE,
)
MULTI_RE = re.compile(r"\bmulti\b|\ball\s+(?:the\s+)?(?:games|borderlands)\b|\bevery\s+game\b", re.IGNORECASE)
OTHER_RE = re.compile(
    r"\bbl\s?[123]\b|\bborderlands\s*[123]\b|\btps\b|\bpre-?sequel\b|\bwonderlands\b|\bgoty\b",
    re.IGNORECASE,
)

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_LEAD = r"(?:expir\w*|valid\s+(?:until|through|thru|till)|until|through|thru)\W{0,4}(?:on\s+)?"
EXPIRY_NUMERIC = re.compile(_LEAD + r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", re.IGNORECASE)
EXPIRY_MONTH_FIRST = re.compile(_LEAD + _MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?", re.IGNORECASE)
EXPIRY_DAY_FIRST = re.compile(_LEAD + r"(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r"(?:,?\s*(\d{4}))?", re.IGNORECASE)


def html_to_text(raw):
    raw = re.split(r"submitted\s+by", raw, maxsplit=1, flags=re.IGNORECASE)[0]
    raw = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(raw)


def parse_expiry(text, posted):
    """Return the expiry as a date, or None. Years are inferred from the post date."""
    def build(month, day, year):
        try:
            if year:
                year = int(year)
                year += 2000 if year < 100 else 0
                return date(year, month, day)
            d = date(posted.year, month, day)
            # "Expires 1/3" in a late-December post means next January.
            return d if d >= posted.date() - timedelta(days=60) else date(posted.year + 1, month, day)
        except ValueError:
            return None

    if m := EXPIRY_NUMERIC.search(text):
        return build(int(m.group(1)), int(m.group(2)), m.group(3))
    if m := EXPIRY_MONTH_FIRST.search(text):
        return build(MONTHS[m.group(1).lower()], int(m.group(2)), m.group(3))
    if m := EXPIRY_DAY_FIRST.search(text):
        return build(MONTHS[m.group(2).lower()], int(m.group(1)), m.group(3))
    return None


def classify(text):
    """Returns 'bl4', 'other' or None (no game mentioned)."""
    if BL4_RE.search(text) or MULTI_RE.search(text):
        return "bl4"
    if OTHER_RE.search(text):
        return "other"
    return None


def extract_codes(title, body, posted):
    """Yield (code, expiry_date) for every code in a post that is valid for Borderlands 4."""
    lines = [l.strip() for l in (title + "\n" + body).splitlines() if l.strip()]
    title_class = classify(title)
    code_lines = {i for i, l in enumerate(lines) if CODE_PATTERN.search(l)}
    unique_codes = {c.upper() for l in lines for c in CODE_PATTERN.findall(l)}
    results = []

    for i in sorted(code_lines):
        # Game context: this line plus up to 3 lines above it, stopping at the previous code
        # so "BL3: AAAAA-..." / "BL4: BBBBB-..." lists don't leak into each other.
        context = [lines[i]]
        for j in range(i - 1, max(-1, i - 4), -1):
            if j in code_lines:
                break
            context.insert(0, lines[j])
        game = classify("\n".join(context)) or title_class
        if game != "bl4":
            continue

        # Expiry: this line and the lines below it up to the next code; a post with a
        # single code may state it anywhere.
        after = [lines[i]]
        for j in range(i + 1, min(len(lines), i + 3)):
            if j in code_lines:
                break
            after.append(lines[j])
        expiry = parse_expiry(" ".join(after), posted)
        if expiry is None and len(unique_codes) == 1:
            expiry = parse_expiry(" ".join(lines), posted)

        for code in CODE_PATTERN.findall(lines[i]):
            results.append((code.upper(), expiry))
    return results


def parse_feed(feed_bytes):
    results = []
    feed = feedparser.parse(feed_bytes)
    for entry in feed.entries:
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if not published:
            continue
        posted = datetime(*published[:6], tzinfo=timezone.utc)
        if posted.timestamp() < BL4_RELEASE_UTC:
            continue

        content = entry.get("content")
        body = html_to_text(content[0]["value"] if content else entry.get("summary", ""))
        title = html.unescape(entry.get("title", ""))
        link = entry.get("link", "")
        source_url = link if link.startswith("https://") else ""

        for code, expiry in extract_codes(title, body, posted):
            results.append({
                "code": code,
                "posted_at": posted.isoformat(),
                "expires_date": expiry.isoformat() if expiry else "",
                "source": "Reddit",
                "source_url": source_url,
            })
    return results


# ---------------- FETCHING ----------------
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/atom+xml, application/rss+xml, */*"})


def fetch_reddit_codes():
    """Returns (results, error, retry_after_seconds)."""
    try:
        resp = session.get(REDDIT_URL, timeout=15)
    except requests.RequestException as e:
        return [], f"Reddit request failed: {e}", None

    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After") or resp.headers.get("x-ratelimit-reset") or "60"
        try:
            wait = float(retry)
        except ValueError:
            wait = 60.0
        return [], "Reddit rate limit hit (429)", wait + 5
    if resp.status_code == 403:
        return [], "Reddit refused the request (403) - backing off", 900
    if resp.status_code != 200:
        return [], f"Reddit returned HTTP {resp.status_code}", None

    try:
        return parse_feed(resp.content), None, None
    except Exception as e:  # malformed feed shouldn't kill the worker
        return [], f"Could not parse Reddit feed: {e}", None


# ---------------- DISCORD ----------------
def send_discord_notification(entry):
    fields = [
        {"name": "Code", "value": f"```{entry['code']}```", "inline": False},
        {"name": "Expires", "value": format_date(entry.get("expires_date")) or "N/A", "inline": True},
    ]
    if entry.get("source_url"):
        fields.append({"name": "Source", "value": f"[{entry['source']}]({entry['source_url']})", "inline": True})
    post_discord({
        "title": "✨ New Borderlands 4 SHiFT Code Found!",
        "color": 15844367,
        "fields": fields,
    })


def post_discord(embed):
    if not DISCORD_WEBHOOK_URL:
        return
    embed = dict(embed, footer={"text": "BL4 SHiFT Code Tracker"}, timestamp=datetime.now(timezone.utc).isoformat())
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"username": "SHiFT Code Bot", "embeds": [embed]}, timeout=10)
    except requests.RequestException as e:
        log(f"⚠️ Discord notification failed: {e}")


# ---------------- BACKGROUND WORKER ----------------
def format_date(iso_date):
    try:
        return date.fromisoformat(iso_date).strftime("%b %d, %Y")
    except (TypeError, ValueError):
        return ""


def is_expired(entry):
    if entry.get("expired_manually"):
        return True
    try:
        return date.fromisoformat(entry.get("expires_date", "")) < date.today()
    except ValueError:
        return False


def check_once():
    """One scrape + merge. Returns (ok, retry_after)."""
    global last_checked_at, last_error
    fetched, error, retry_after = fetch_reddit_codes()
    if error:
        last_error = error
        log(f"⚠️ {error}")
        return False, retry_after

    now_iso = datetime.now(timezone.utc).isoformat()
    new_entries = []
    with state_lock:
        seen = load_state()
        for item in fetched:
            code = item["code"]
            if code not in seen:
                seen[code] = {
                    "code": code,
                    "found_at": now_iso,
                    "posted_at": item["posted_at"],
                    "expires_date": item["expires_date"],
                    "activated": False,
                    "expired_manually": False,
                    "source": item["source"],
                    "source_url": item["source_url"],
                }
                new_entries.append(seen[code])
                log(f"✨ New code found: {code}")
            else:
                existing = seen[code]
                if item["expires_date"] and existing.get("expires_date") != item["expires_date"]:
                    existing["expires_date"] = item["expires_date"]
                if not existing.get("source_url") and item["source_url"]:
                    existing["source_url"] = item["source_url"]
                if not existing.get("posted_at"):
                    existing["posted_at"] = item["posted_at"]
        save_state(seen)

    # Only announce recent codes so importing the feed's backlog doesn't flood Discord.
    cutoff = datetime.now(timezone.utc) - timedelta(days=NOTIFY_MAX_AGE_DAYS)
    for entry in new_entries:
        posted = _parse_iso(entry["posted_at"])
        if posted and posted >= cutoff and not is_expired(entry):
            send_discord_notification(entry)
            time.sleep(DISCORD_DELAY)  # stay under Discord's webhook rate limit

    last_checked_at = datetime.now().strftime("%b %d, %Y, %H:%M")
    last_error = None
    return True, None


def next_wait(failures, retry_after):
    """Seconds to sleep after a check: the normal interval, or an exponential back-off."""
    if failures == 0:
        return CHECK_INTERVAL
    return max(retry_after or 0, min(CHECK_INTERVAL, 60 * 2 ** failures)) + random.uniform(0, 15)


def track_outage(failures, error, alerted):
    """Send one Discord alert when checks keep failing and one when they recover.
    Returns the new 'alerted' flag."""
    if failures >= ALERT_AFTER_FAILURES and not alerted:
        post_discord({
            "title": "⚠️ SHiFT tracker can't reach Reddit",
            "color": 15158332,
            "description": f"{failures} checks in a row failed.\nLast error: {error}",
        })
        return True
    if failures == 0 and alerted:
        post_discord({"title": "✅ SHiFT tracker is working again", "color": 3066993})
        return False
    return alerted


def background_code_checker():
    global last_error
    failures = 0
    alerted = False
    while True:
        wake_event.clear()
        log("🕒 Checking for new SHiFT codes...")
        try:
            ok, retry_after = check_once()
        except Exception as e:  # keep the worker alive whatever happens
            last_error = f"Unexpected error: {e}"
            log(f"⚠️ Unexpected error during check: {e}")
            ok, retry_after = False, None

        failures = 0 if ok else failures + 1
        alerted = track_outage(failures, last_error, alerted)
        wait = next_wait(failures, retry_after)
        if ok:
            log(f"✅ Check complete. Next check in {wait / 60:.0f} minutes.")
        else:
            log(f"Retrying in {wait / 60:.1f} minutes.")
        wake_event.wait(wait)  # a manual "check now" sets this event


# ---------------- API ENDPOINTS ----------------
def sort_key(entry):
    dt = _parse_iso(entry.get("posted_at")) or _parse_iso(entry.get("found_at"))
    return dt or datetime.fromtimestamp(0, timezone.utc)


def public_view(entry):
    found = _parse_iso(entry.get("found_at"))
    return {
        "code": entry["code"],
        "source": entry.get("source", ""),
        "source_url": entry.get("source_url", ""),
        "posted_at": entry.get("posted_at") or entry.get("found_at"),
        "found": found.astimezone().strftime("%b %d, %Y, %H:%M") if found else "",
        "expires": format_date(entry.get("expires_date")) or entry.get("expires", ""),
        "activated": bool(entry.get("activated")),
        "expired_manually": bool(entry.get("expired_manually")),
        "expired": is_expired(entry),
    }


def sorted_entries(seen):
    return sorted(seen.values(), key=sort_key, reverse=True)


@app.route("/")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "index.html")


@app.route("/api/codes", methods=["GET", "POST"])
def handle_codes():
    if request.method == "GET":
        with state_lock:
            seen = load_state()
        return jsonify({
            "codes": [public_view(e) for e in sorted_entries(seen)],
            "last_checked": last_checked_at,
            "last_error": last_error,
        })

    data = request.get_json(silent=True) or {}
    field = {"activated": "activated", "expired": "expired_manually"}.get(data.get("state"))
    if field is None or not isinstance(data.get("value"), bool) or not isinstance(data.get("code"), str):
        return jsonify({"status": "error", "message": "invalid request"}), 400
    with state_lock:
        seen = load_state()
        if data["code"] not in seen:
            return jsonify({"status": "error", "message": "unknown code"}), 404
        seen[data["code"]][field] = data["value"]
        save_state(seen)
    return jsonify({"status": "success"})


@app.route("/api/check-now", methods=["POST"])
def check_now():
    global last_manual_check
    wait = MANUAL_CHECK_COOLDOWN - (time.time() - last_manual_check)
    if wait > 0:
        return jsonify({"status": "cooldown", "retry_in": int(wait) + 1}), 429
    last_manual_check = time.time()
    wake_event.set()
    return jsonify({"status": "started"})


@app.route("/api/steam-json")
def steam_json():
    with state_lock:
        seen = load_state()
    return jsonify({
        e["code"]: {
            "expires": format_date(e.get("expires_date")) or e.get("expires", ""),
            "found": public_view(e)["found"],
            "source_url": e.get("source_url", ""),
        }
        for e in sorted_entries(seen) if not is_expired(e)
    })


@app.route("/api/steam-bbcode")
def steam_bbcode():
    with state_lock:
        seen = load_state()
    active = [e for e in sorted_entries(seen) if not is_expired(e)]
    text = "[table]\n[tr][th]Shift Code[/th][th]Found[/th][/tr]\n"
    for e in active:
        text += f"[tr][td]{e['code']}[/td][td]{public_view(e)['found']}[/td][/tr]\n"
    text += "[/table]"
    return jsonify({"content": text})


@app.route("/api/logs")
def get_logs():
    with log_lock:
        return jsonify(list(LOGS))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.makedirs(DATA_DIR, exist_ok=True)
    log(f"State file: {STORAGE_FILE}")
    threading.Thread(target=background_code_checker, daemon=True).start()
    log(f"Dashboard: http://{HOST}:{PORT}")
    waitress.serve(app, host=HOST, port=PORT, threads=4)
