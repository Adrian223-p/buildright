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
 
import re, urllib.parse
 
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BuildRight/1.0)",
}
 
# Fallback sheet names matching the Renovation_Materials_Reference.xlsx tabs
KNOWN_SHEET_NAMES = [
    "🪵 Flooring",
    "🧱 Tile & Wall",
    "🍳 Countertops",
    "🎨 Paint & Finishes",
    "🚪 Cabinetry & Millwork",
    "🚿 Plumbing Fixtures",
    "🏗️ Structure & Insulation",
    "📋 Project Quote",
]
 
 
def get_sheet_names(sheet_id: str) -> list[str]:
    """
    Try to auto-detect sheet tab names from the published HTML page.
    Falls back to the known sheet names from the user's spreadsheet.
    """
    try:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/pubhtml"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        names = re.findall(r'<li[^>]*>\s*<a[^>]*>([^<]+)</a>', html)
        names = [n.strip() for n in names if n.strip()]
        if len(names) >= 2:
            return names
    except Exception:
        pass
    return KNOWN_SHEET_NAMES
 
 
def fetch_sheet_by_name(sheet_id: str, sheet_name: str) -> list[list[str]]:
    """
    Fetch one sheet tab using the gviz/tq endpoint.
    This works for ANY sheet shared as 'Anyone with the link can view'
    — no API key, no OAuth, no 'Publish to web' required.
    """
    encoded = urllib.parse.quote(sheet_name)
    url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={encoded}"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    rows = []
    for row in csv.reader(io.StringIO(raw)):
        cleaned = [c.strip() for c in row]
        if any(cleaned):
            rows.append(cleaned)
    return rows
 
 
def load_google_sheet_data(sheet_id: str) -> str:
    """
    Downloads all tabs from the Google Sheet and returns structured
    text for the AI. Uses gviz/tq — works for any publicly shared sheet.
    """
    if not sheet_id:
        return "[ERROR: GOOGLE_SHEET_ID not set in Render environment variables.]"
 
    sheet_names = get_sheet_names(sheet_id)
    sections = []
 
    for name in sheet_names:
        try:
            rows = fetch_sheet_by_name(sheet_id, name)
        except Exception as e:
            sections.append(f"\n=== SHEET: {name} ===\n[Could not load: {e}]")
            continue
 
        if not rows:
            continue
 
        lines = [f"\n=== SHEET: {name} ==="]
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
        names = get_sheet_names(GOOGLE_SHEET_ID)
        return jsonify({"tabs": [{"title": n} for n in names], "count": len(names)})
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
