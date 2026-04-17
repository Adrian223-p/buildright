"""
BuildRight Quote Server — Cloud Edition
----------------------------------------
Reads pricing data live from a Google Sheet on every query.
Deployed on Render.com — runs 24/7 with zero maintenance.
 
Environment variables required (set in Render dashboard):
  ANTHROPIC_API_KEY   — your Anthropic API key
  GOOGLE_SHEET_ID     — the ID from your Google Sheet URL
"""
 
import os, json, csv, io, traceback, pathlib
import urllib.request, urllib.error, urllib.parse
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
 
app = Flask(__name__, static_folder=".")
CORS(app)
 
# ── Config from environment variables ────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_SHEET_ID   = os.environ.get("GOOGLE_SHEET_ID",   "")
PORT              = int(os.environ.get("PORT", 5050))
 
# ══════════════════════════════════════════════════════════════════════════════
#  GOOGLE SHEETS READER
#  Reads every sheet via the public CSV export endpoint.
#  No API key needed — sheet just needs to be shared as "Anyone can view".
# ══════════════════════════════════════════════════════════════════════════════
 
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BuildRight/1.0)",
}
 
 
def fetch_csv_from_url(url: str) -> list[list[str]]:
    """Fetch a CSV URL and return as a list of rows, skipping blank rows."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    rows = []
    for row in csv.reader(io.StringIO(raw)):
        cleaned = [c.strip() for c in row]
        if any(cleaned):
            rows.append(cleaned)
    return rows
 
 
def get_sheet_tabs(sheet_id: str) -> list[dict]:
    """
    Get all tab names and GIDs from the publicly published HTML page.
    This works for any sheet with 'Publish to web' enabled — no login needed.
    Falls back to a single default tab if parsing fails.
    """
    import re
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/pubhtml"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # pubhtml contains links like: gid=123456789
        gid_title = re.findall(r'gid=(\d+)[^"]*"[^>]*>([^<]+)</a>', html)
        if gid_title:
            seen = set()
            tabs = []
            for gid, title in gid_title:
                title = title.strip()
                if gid not in seen and title:
                    seen.add(gid)
                    tabs.append({"gid": gid, "title": title})
            if tabs:
                return tabs
    except Exception:
        pass
    return [{"gid": "0", "title": "Sheet1"}]
 
 
def load_google_sheet_data(sheet_id: str) -> str:
    """
    Downloads all tabs from the published Google Sheet and returns
    structured text for the AI to reason over.
    Uses /pub?output=csv which works for 'Publish to web' sheets.
    """
    if not sheet_id:
        return "[ERROR: GOOGLE_SHEET_ID not set in Render environment variables.]"
 
    try:
        tabs = get_sheet_tabs(sheet_id)
    except Exception as e:
        return f"[ERROR fetching sheet tabs: {e}]"
 
    sections = []
    for tab in tabs:
        gid   = tab["gid"]
        title = tab["title"]
        try:
            # Use /pub?output=csv — works for "Publish to web" sheets, no auth needed
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/pub?gid={gid}&single=true&output=csv"
            rows = fetch_csv_from_url(url)
        except Exception as e:
            sections.append(f"\n=== SHEET: {title} ===\n[Could not load: {e}]")
            continue
 
        if not rows:
            continue
 
        lines = [f"\n=== SHEET: {title} ==="]
        for row in rows:
            while row and row[-1] == "":
                row.pop()
            if row:
                lines.append(" | ".join(row))
        sections.append("\n".join(lines))
 
    if not sections:
        return "[No data found in Google Sheet]"
 
    header = (
        "LIVE PRICING DATA — read fresh from Google Sheets for this query.\n"
        "All prices in CAD. Use this as your primary pricing reference.\n"
        "Column format: # | Material Name | Type/Grade | "
        "Low ($/unit) | Mid ($/unit) | High ($/unit) | Install Add | Notes\n"
    )
    return header + "\n".join(sections)
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT  (injected with live sheet data on every request)
# ══════════════════════════════════════════════════════════════════════════════
 
SYSTEM_TEMPLATE = """You are an expert renovation and remodelling estimator for a professional contracting company in Canada. Your ONLY job is to generate accurate, detailed, itemized renovation quotes for the company owner.
 
CRITICAL: Your pricing data is provided LIVE from the owner's Google Sheet below. You MUST use those exact prices. If a price is missing from the sheet, say so and ask the owner to add it.
 
## QUOTING RULES
1. Always provide a FULL itemized quote as a markdown table:
   | Item | Description | Qty | Unit | Unit Cost (CAD) | Total (CAD) |
2. Always add these rows at the bottom of every quote:
   - Subtotal
   - Overhead & Profit (20% of subtotal)
   - Contingency (5% of subtotal)
   - HST 13% (on subtotal + overhead + contingency)
   - **GRAND TOTAL**
3. When finish grade isn't specified, assume MID-RANGE and note it.
4. Round all dollar amounts to the nearest whole dollar.
5. Show labour calculations inline: e.g. "8 hrs × $75/hr = $600"
6. State key assumptions clearly at the top of each quote.
7. End every quote with: "*This is an estimate. Final pricing may vary based on site conditions, exact material selection, and current supplier pricing.*"
8. For material comparisons, pull the exact figures from the sheet data below.
9. Only respond about renovation quoting. Redirect anything unrelated.
 
## STANDARD LABOUR RATES
General labour $45-55/hr | Carpenter rough $70-80/hr | Carpenter finish $80-90/hr
Painter $60-70/hr | Tile setter $75-85/hr | Flooring installer $65-75/hr
Plumber $115-125/hr | Electrician $110-120/hr | HVAC $105-115/hr
Drywall $65-80/hr | Cabinet installer $75-85/hr | Demo $55-65/hr | PM $105-115/hr
 
## PERMIT COSTS (Ontario)
Kitchen $350-600 | Bathroom $200-400 | Basement $500-900
Full home $2,500-6,000 | Deck $300-600 | Electrical upgrade $300-500
 
---
## LIVE PRICING DATA FROM GOOGLE SHEET (use these prices):
 
{sheet_data}
"""
 
# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════
 
@app.route("/")
def serve_index():
    return send_from_directory(".", "index.html")
 
 
@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory(".", filename)
 
 
@app.route("/api/status")
def status():
    key_ok   = bool(ANTHROPIC_API_KEY)
    sheet_ok = bool(GOOGLE_SHEET_ID)
    return jsonify({
        "server":       "running",
        "api_key_set":  key_ok,
        "sheet_id_set": sheet_ok,
        "sheet_id":     GOOGLE_SHEET_ID[:8] + "…" if GOOGLE_SHEET_ID else "",
    })
 
 
@app.route("/api/sheet-info")
def sheet_info():
    if not GOOGLE_SHEET_ID:
        return jsonify({"error": "GOOGLE_SHEET_ID not set"}), 400
    try:
        tabs = get_sheet_tabs(GOOGLE_SHEET_ID)
        return jsonify({"tabs": tabs, "count": len(tabs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
 
 
@app.route("/api/chat", methods=["POST"])
def chat():
    try:
        body      = request.get_json(force=True)
        messages  = body.get("messages", [])
        api_key   = body.get("api_key", "").strip() or ANTHROPIC_API_KEY
 
        if not api_key:
            return jsonify({"error":
                "ANTHROPIC_API_KEY not set. Add it in your Render dashboard "
                "under Environment Variables."}), 400
 
        # ── Read Google Sheet fresh every request ──────────────────────────
        sheet_data    = load_google_sheet_data(GOOGLE_SHEET_ID)
        system_prompt = SYSTEM_TEMPLATE.format(sheet_data=sheet_data)
        sheet_loaded  = not sheet_data.startswith("[ERROR")
 
        # ── Call Anthropic API ─────────────────────────────────────────────
        payload = json.dumps({
            "model":      "claude-sonnet-4-6",
            "max_tokens": 4096,
            "system":     system_prompt,
            "messages":   messages,
        }).encode("utf-8")
 
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
 
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
 
        reply = data.get("content", [{}])[0].get("text", "No response.")
        return jsonify({
            "reply":            reply,
            "sheet_loaded":     sheet_loaded,
            "model":            data.get("model", ""),
            "usage":            data.get("usage", {}),
        })
 
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:    msg = json.loads(err_body).get("error", {}).get("message", err_body)
        except: msg = err_body
        return jsonify({"error": f"Anthropic API error {e.code}: {msg}"}), e.code
 
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500
 
 
if __name__ == "__main__":
    print(f"\n  BuildRight Quote Server (Cloud Edition)")
    print(f"  API key : {'✓ set' if ANTHROPIC_API_KEY else '✗ missing'}")
    print(f"  Sheet ID: {'✓ ' + GOOGLE_SHEET_ID[:12] + '…' if GOOGLE_SHEET_ID else '✗ missing'}")
    print(f"  Port    : {PORT}\n")
    app.run(host="0.0.0.0", port=PORT)
