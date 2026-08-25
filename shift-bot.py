import requests
import re
import json
import feedparser
from datetime import datetime
import time
from flask import Flask, jsonify, request
import threading
import io

# ---------------- CONFIG ----------------
STORAGE_FILE = "shift_codes_state.json"
CHECK_INTERVAL = 30 * 60  # 30 minutes
REDDIT_URL = "https://www.reddit.com/r/BorderlandsShiftCodes/new.json?limit=50"
HEADERS = {"User-Agent": "BL4CodeTracker/1.0 (by /u/VaultHunter_Alpha)"}
TWITTER_ACCOUNTS = ["GearboxOfficial", "Borderlands"]
import os
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
BL4_KEYWORDS = ["borderlands 4", "bl4"]
OTHER_GAMES_KEYWORDS = [
    r"\bbl2\b", r"\bborderlands 2\b",
    r"\bbl3\b", r"\bborderlands 3\b",
    r"\btps\b", r"\bborderlands: the pre-sequel\b",
    r"\bborderlands goty\b",
    r"\bwonderlands\b"
]
OTHER_GAMES_REGEX = re.compile("|".join(OTHER_GAMES_KEYWORDS), re.IGNORECASE)
BL4_RELEASE_UTC = 1757702400  # Sep 12, 2025 UTC
# ----------------------------------------

app = Flask(__name__)

# GLOBAL STATE & LOGGING SYSTEM
last_checked_at = None
LOGS = []
MAX_LOGS = 100

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{timestamp}] {message}"
    print(full_msg)
    global LOGS
    LOGS.insert(0, full_msg)
    if len(LOGS) > MAX_LOGS:
        LOGS = LOGS[:MAX_LOGS]

def load_codes_and_state():
    try:
        with open(STORAGE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_codes_and_state(codes):
    with open(STORAGE_FILE, "w") as f:
        json.dump(codes, f, indent=2)

CODE_PATTERN = re.compile(r"[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}", re.IGNORECASE)
EXPIRY_PATTERN = re.compile(r"(?:expir(?:es|y)|valid\s+until)[:\-]?\s*(\w+\s\d{1,2},?\s?\d{4}?)", re.IGNORECASE)

# ---------------- DISCORD NOTIFICATION ----------------
def send_discord_notification(new_code_data):
    if not DISCORD_WEBHOOK_URL: return

    embed = {
        "title": "✨ New Borderlands 4 SHiFT Code Found!",
        "color": 15844367,
        "fields": [
            {"name": "Code", "value": f"```{new_code_data['code']}```", "inline": False},
            {"name": "Expires", "value": new_code_data['expires'] or "N/A", "inline": True},
            {"name": "Source", "value": f"[{new_code_data['source']}]({new_code_data['source_url']})", "inline": True}
        ],
        "footer": {"text": "BL4 SHiFT Code Tracker"},
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"username": "SHiFT Code Bot", "embeds": [embed]}, timeout=10)
    except Exception as e:
        log(f"⚠️ Discord notification failed: {e}")

# ---------------- FETCHERS ----------------
def fetch_reddit_codes():
    results = []
    try:
        resp = requests.get(REDDIT_URL, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log(f"⚠️ Error fetching from Reddit: {e}")
        return results

    posts = data.get("data", {}).get("children", [])
    for post in posts:
        pdata = post.get("data", {})
        if pdata.get("created_utc", 0) < BL4_RELEASE_UTC: continue

        title = pdata.get("title", "").lower()
        body = pdata.get("selftext", "").lower()
        full_text = title + "\n" + body
        permalink = pdata.get("permalink", "")
        source_url = f"https://www.reddit.com{permalink}" if permalink else REDDIT_URL

        lines = [l.strip() for l in full_text.splitlines() if l.strip()]
        for i, line in enumerate(lines):
            codes = CODE_PATTERN.findall(line)
            if not codes: continue
            
            start = max(0, i-3)
            context = " ".join(lines[start:i+1]).lower()
            if not (any(k in context for k in BL4_KEYWORDS) and not OTHER_GAMES_REGEX.search(context)):
                continue

            expiry_match = EXPIRY_PATTERN.search(context)
            expiry = expiry_match.group(1) if expiry_match else ""
            for code in codes:
                results.append({
                    "code": code.upper(), 
                    "expires": expiry, 
                    "source": "Reddit",
                    "source_url": source_url
                })
    return results

def fetch_twitter_codes():
    results = []
    for user in TWITTER_ACCOUNTS:
        try:
            feed = feedparser.parse(f"https://twitrss.me/twitter_user_to_rss/?user={user}")
        except Exception as e:
            log(f"⚠️ Error fetching Twitter {user}: {e}")
            continue

        for entry in feed.entries:
            text = (entry.title + " " + entry.get("description", "")).lower()
            if not hasattr(entry, 'published_parsed') or entry.published_parsed is None: continue
            if time.mktime(entry.published_parsed) < BL4_RELEASE_UTC: continue
            if not any(k in text for k in BL4_KEYWORDS): continue
            if OTHER_GAMES_REGEX.search(text): continue

            codes = CODE_PATTERN.findall(text)
            expiry_match = EXPIRY_PATTERN.search(text)
            expiry = expiry_match.group(1) if expiry_match else ""
            for code in codes:
                results.append({
                    "code": code.upper(), 
                    "expires": expiry, 
                    "source": "Twitter",
                    "source_url": entry.get("link", "")
                })
    return results

# ---------------- BACKGROUND WORKER ----------------
def background_code_checker():
    global last_checked_at
    while True:
        log("🕒 Checking for new SHiFT codes...")
        seen_codes = load_codes_and_state()
        fetched = fetch_reddit_codes() + fetch_twitter_codes()
        now_str = datetime.now().strftime("%b %d, %Y, %H:%M")
        
        for item in fetched:
            code = item["code"]
            if code not in seen_codes:
                log(f"✨ New code found: {code}")
                new_obj = {
                    "code": code,
                    "found": now_str,
                    "expires": item["expires"],
                    "activated": False,
                    "expired_manually": False,
                    "source": item["source"],
                    "source_url": item.get("source_url", "")
                }
                seen_codes[code] = new_obj
                send_discord_notification(new_obj)
            else:
                if seen_codes[code].get("expires") != item.get("expires"):
                    seen_codes[code]["expires"] = item.get("expires")
                if not seen_codes[code].get("source_url") and item.get("source_url"):
                    seen_codes[code]["source_url"] = item.get("source_url")

        save_codes_and_state(seen_codes)
        last_checked_at = now_str
        log(f"✅ Check complete. Next check in {CHECK_INTERVAL / 60:.0f} minutes.")
        time.sleep(CHECK_INTERVAL)

# ---------------- API ENDPOINTS ----------------
@app.route('/')
def index():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>BL4 SHiFT Codes</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap');
            body { font-family: 'Inter', sans-serif; background-color: #0d1117; color: #c9d1d9; }
            .container { max-width: 1000px; }
            .code-table { width: 100%; border-collapse: collapse; }
            .code-table th, .code-table td { padding: 12px; border-bottom: 1px solid #30363d; text-align: left; }
            .code-table th { background-color: #161b22; font-weight: bold; text-transform: uppercase; letter-spacing: 0.05em; }
            .code-table tr:hover { background-color: #21262d; }
            .code-table tbody tr.expired { color: #8b949e; text-decoration: line-through; }
            .copy-button { background-color: #238636; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; min-width: 80px; }
            .copy-button:hover { background-color: #2ea043; }
            .header-text { background-clip: text; -webkit-background-clip: text; color: transparent; background-image: linear-gradient(to right, #63a4ff, #8338ec); }
            input[type="checkbox"] { appearance: none; width: 18px; height: 18px; border: 2px solid #58a6ff; border-radius: 4px; cursor: pointer; position: relative; }
            input[type="checkbox"]:checked { background-color: #58a6ff; }
            input[type="checkbox"]:checked::before { content: '✓'; color: #0d1117; font-size: 14px; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); }
            .modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background-color: rgba(0, 0, 0, 0.7); display: none; align-items: center; justify-content: center; z-index: 1000; }
            .modal-content { background-color: #161b22; padding: 24px; border-radius: 8px; max-width: 90%; max-height: 90%; overflow: auto; }
            .modal-content pre { background-color: #0d1117; padding: 16px; border-radius: 4px; white-space: pre-wrap; word-wrap: break-word; }
            .log-entry { font-family: monospace; font-size: 12px; border-bottom: 1px solid #30363d; padding: 4px 0; }
            a { color: #58a6ff; text-decoration: none; }
            a:hover { text-decoration: underline; }
        </style>
    </head>
    <body class="p-6">
        <div class="container mx-auto p-8 bg-[#161b22] rounded-lg shadow-lg">
            <h1 class="text-3xl font-bold mb-6 text-center header-text">BL4 SHiFT Code Tracker</h1>
            <p class="text-center mb-4 text-[#8b949e]">Automatically fetches codes from Reddit and Twitter.</p>
            <p id="last-checked" class="text-center text-sm text-[#8b949e] mb-8"></p>
            
            <div class="flex flex-wrap justify-center gap-4 mb-8">
                <button onclick="showSteamJson()" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded">Show Steam JSON</button>
                <button onclick="showSteamBbcode()" class="bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-2 px-4 rounded">Show Steam BBCode</button>
                <button onclick="showLogs()" class="bg-gray-600 hover:bg-gray-700 text-white font-bold py-2 px-4 rounded">Show Logs</button>
                <a href="https://shift.gearboxsoftware.com/rewards" target="_blank" class="bg-purple-600 hover:bg-purple-700 text-white font-bold py-2 px-4 rounded flex items-center">Activate Codes</a>
            </div>

            <table class="code-table rounded-lg overflow-hidden">
                <thead>
                    <tr>
                        <th class="rounded-tl-lg">Code</th><th>Source</th><th>Found</th><th>Expires</th><th class="text-center">Activated</th><th class="rounded-tr-lg text-center">Expired</th>
                    </tr>
                </thead>
                <tbody id="code-list">
                    <tr><td colspan="6" class="text-center py-4 text-[#8b949e]">Loading codes...</td></tr>
                </tbody>
            </table>
        </div>

        <div id="steam-modal" class="modal-overlay"><div class="modal-content"><h2 class="text-2xl font-bold mb-4">Steam Formatted JSON</h2><pre id="steam-json-content" class="text-sm"></pre><div class="mt-4 flex justify-end gap-2"><button onclick="copyElementText('steam-json-content', this)" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded">Copy</button><button onclick="closeModals()" class="bg-gray-500 hover:bg-gray-600 text-white font-bold py-2 px-4 rounded">Close</button></div></div></div>
        <div id="bbcode-modal" class="modal-overlay"><div class="modal-content"><h2 class="text-2xl font-bold mb-4">Steam Formatted BBCode</h2><pre id="bbcode-content" class="text-sm"></pre><div class="mt-4 flex justify-end gap-2"><button onclick="copyElementText('bbcode-content', this)" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded">Copy</button><button onclick="closeModals()" class="bg-gray-500 hover:bg-gray-600 text-white font-bold py-2 px-4 rounded">Close</button></div></div></div>
        <div id="logs-modal" class="modal-overlay"><div class="modal-content" style="width: 800px;"><h2 class="text-2xl font-bold mb-4">System Logs</h2><div id="logs-content" class="bg-[#0d1117] p-4 rounded h-64 overflow-y-auto"></div><div class="mt-4 flex justify-end gap-2"><button onclick="fetchLogs()" class="bg-green-600 hover:bg-green-700 text-white font-bold py-2 px-4 rounded">Refresh</button><button onclick="closeModals()" class="bg-gray-500 hover:bg-gray-600 text-white font-bold py-2 px-4 rounded">Close</button></div></div></div>

        <script>
            async function copyToClipboard(text, btn) {
                let success = false;
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    try { await navigator.clipboard.writeText(text); success = true; } 
                    catch (err) { console.warn("Clipboard API failed"); }
                }
                
                if (!success) {
                    try {
                        const ta = document.createElement("textarea"); ta.value = text; 
                        ta.style.position = "fixed"; ta.style.left = "-9999px";
                        document.body.appendChild(ta); ta.focus(); ta.select();
                        success = document.execCommand('copy'); 
                        document.body.removeChild(ta);
                    } catch (err) { console.error('Copy failed.'); }
                }

                if (success && btn) {
                    const original = btn.textContent;
                    btn.textContent = 'Copied!';
                    setTimeout(() => btn.textContent = original, 2000);
                }
            }
            
            function copyElementText(id, btn) { copyToClipboard(document.getElementById(id).textContent, btn); }
            function closeModals() { document.querySelectorAll('.modal-overlay').forEach(m => m.style.display = 'none'); }

            async function fetchCodes() {
                try {
                    const response = await fetch('/api/codes');
                    renderCodes(await response.json());
                } catch (error) { console.error('Error:', error); }
            }

            function renderCodes(data) {
                document.getElementById('last-checked').textContent = `Last checked: ${data.last_checked || 'Never'}`;
                const tbody = document.getElementById('code-list');
                tbody.innerHTML = '';
                if (!data.codes.length) { tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-[#8b949e]">No codes found.</td></tr>'; return; }
                
                data.codes.forEach(c => {
                    const row = document.createElement('tr');
                    if (c.expired_manually) row.className = 'expired';
                    const sourceLink = c.source_url ? `<a href="${c.source_url}" target="_blank">${c.source}</a>` : c.source;
                    row.innerHTML = `
                        <td class="font-mono"><span class="mr-4">${c.code}</span><button class="copy-button float-right" onclick="copyToClipboard('${c.code}', this)">Copy</button></td>
                        <td>${sourceLink}</td>
                        <td>${c.found}</td><td>${c.expires || 'N/A'}</td>
                        <td class="text-center"><input type="checkbox" data-code="${c.code}" data-state="activated" ${c.activated ? 'checked' : ''}></td>
                        <td class="text-center"><input type="checkbox" data-code="${c.code}" data-state="expired" ${c.expired_manually ? 'checked' : ''}></td>
                    `;
                    tbody.appendChild(row);
                });
                
                document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                    cb.addEventListener('change', async (e) => {
                        await fetch('/api/codes', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({code: e.target.dataset.code, state: e.target.dataset.state, value: e.target.checked})
                        });
                        fetchCodes();
                    });
                });
            }

            async function showSteamJson() {
                const res = await fetch('/api/steam-json');
                document.getElementById('steam-json-content').textContent = JSON.stringify(await res.json(), null, 2);
                document.getElementById('steam-modal').style.display = 'flex';
            }
            async function showSteamBbcode() {
                const res = await fetch('/api/steam-bbcode');
                document.getElementById('bbcode-content').textContent = (await res.json()).content;
                document.getElementById('bbcode-modal').style.display = 'flex';
            }
            async function showLogs() { await fetchLogs(); document.getElementById('logs-modal').style.display = 'flex'; }
            async function fetchLogs() {
                const res = await fetch('/api/logs');
                document.getElementById('logs-content').innerHTML = (await res.json()).map(l => `<div class="log-entry">${l}</div>`).join('');
            }

            document.addEventListener('DOMContentLoaded', () => { fetchCodes(); setInterval(fetchCodes, 60000); });
        </script>
    </body>
    </html>
    """

@app.route('/api/codes', methods=['GET', 'POST'])
def handle_codes():
    if request.method == 'GET':
        seen = load_codes_and_state()
        return jsonify({"codes": sorted(seen.values(), key=lambda x: x["found"], reverse=True), "last_checked": last_checked_at})
    
    data = request.get_json()
    seen = load_codes_and_state()
    if data['code'] in seen:
        if data['state'] == "activated": seen[data['code']]["activated"] = data['value']
        elif data['state'] == "expired": seen[data['code']]["expired_manually"] = data['value']
        save_codes_and_state(seen)
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 404

@app.route('/api/steam-json')
def steam_json():
    seen = load_codes_and_state()
    return jsonify({k: {"expires": v.get("expires", ""), "found": v.get("found", ""), "source_url": v.get("source_url", "")} 
                    for k, v in seen.items() if not v.get("expired_manually")})

@app.route('/api/steam-bbcode')
def steam_bbcode():
    seen = load_codes_and_state()
    # Explicitly filter out expired codes
    active_codes = sorted([c for c in seen.values() if not c.get("expired_manually")], key=lambda x: x["found"], reverse=True)
    
    # Generate TABLE Format (Without "Expired" column)
    text = "[table]\n"
    text += "[tr][th]Shift Code[/th][th]Found[/th][/tr]\n"
    
    for c in active_codes:
        text += f"[tr][td]{c['code']}[/td][td]{c['found']}[/td][/tr]\n"
        
    text += "[/table]"
    return jsonify({"content": text})

@app.route('/api/logs')
def get_logs():
    return jsonify(LOGS)

if __name__ == "__main__":
    threading.Thread(target=background_code_checker, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)