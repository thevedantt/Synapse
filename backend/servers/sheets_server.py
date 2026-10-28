"""Google Sheets MCP server: exports tables into ONE team spreadsheet, a new tab per export.

Auth: a Google service account (robot user). The spreadsheet is owned by a real person and
shared with the robot as Editor, so the agent can only write to that one file.
No Google SDK needed: we sign the OAuth JWT with pyjwt and call the REST API.
"""

import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import jwt
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv()
BACKEND_DIR = Path(__file__).parent.parent
KEY_FILE = BACKEND_DIR / os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google-service-account.json")
SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
API = "https://sheets.googleapis.com/v4/spreadsheets"
MAX_ROWS = 1000

mcp = MCPServer("sheets", instructions="Export tables to the team's Google Sheet.")


def http(method: str, url: str, body: dict | bytes | None = None, token: str | None = None) -> dict:
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body else None)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    headers["Content-Type"] = "application/x-www-form-urlencoded" if isinstance(body, bytes) else "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read() or b"{}")


def access_token() -> str:
    """Service account login: signed JWT -> short-lived Google access token."""
    key = json.loads(KEY_FILE.read_text(encoding="utf-8"))
    now = int(time.time())
    assertion = jwt.encode({
        "iss": key["client_email"], "scope": "https://www.googleapis.com/auth/spreadsheets",
        "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 600,
    }, key["private_key"], algorithm="RS256")
    form = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion,
    }).encode()
    return http("POST", "https://oauth2.googleapis.com/token", form)["access_token"]


@mcp.tool()
def export_to_sheet(title: str, headers: list[str], rows: list[list[str | int | float | None]]) -> str:
    """Export a table to the team's Google Sheet as a new tab. Only use when the user asks
    to export/put/save data in Google Sheets. FIRST get the data with run_sql, then pass the
    column names in `headers` and the rows in `rows` (plain numbers, no ₹ or commas).
    Returns a link to the new tab: give it to the user as a Markdown link."""
    if not SHEET_ID or not KEY_FILE.exists():
        return json.dumps({"error": "Google Sheets is not set up yet."})
    if len(rows) > MAX_ROWS:
        return json.dumps({"error": f"Too many rows ({len(rows)}); max is {MAX_ROWS}. Aggregate first."})
    # Tab names can't contain []:*?/\ ; add time so every export gets its own tab.
    tab = f"{re.sub(r'[\[\]:*?/\\]', ' ', title)[:80].strip()} {datetime.now():%d-%b %H.%M}"
    tab_id = secrets.randbelow(2**31)
    try:
        token = access_token()
        http("POST", f"{API}/{SHEET_ID}:batchUpdate", {"requests": [
            {"addSheet": {"properties": {"sheetId": tab_id, "title": tab, "gridProperties": {"frozenRowCount": 1}}}},
            {"repeatCell": {  # bold header row
                "range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }},
        ]}, token)
        cells = urllib.parse.quote(f"'{tab}'!A1")
        http("PUT", f"{API}/{SHEET_ID}/values/{cells}?valueInputOption=USER_ENTERED",
             {"values": [headers, *rows]}, token)
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"Google Sheets rejected it ({e.code}): {e.read().decode()[:300]}"})
    except urllib.error.URLError as e:
        return json.dumps({"error": f"Could not reach Google: {e.reason}"})
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid={tab_id}"
    return json.dumps({"ok": True, "tab": tab, "rows": len(rows), "url": url})


if __name__ == "__main__":
    mcp.run()
