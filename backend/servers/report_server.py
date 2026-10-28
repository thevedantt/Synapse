"""Report MCP server: a cheaper "worker" model turns the orchestrator's findings into a
polished, downloadable business report.

Orchestrator (DeepSeek) gathers the data and decides; worker (gpt-oss) does the long writing.
The requesting user comes from MCP request metadata (set by the API, not the LLM).
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from dotenv import load_dotenv
from mcp.server.mcpserver import Context, MCPServer

load_dotenv()
WORKER_MODEL = os.environ.get("WORKER_MODEL", "openai.gpt-oss-120b-1:0")
# ponytail: reports on local disk; move to S3 when deployed on more than one machine
REPORTS_DIR = Path(__file__).parent.parent / "reports"
client = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])

WRITER_PROMPT = """You write clear, professional business reports for an Indian company.
Always write in English, in Markdown. Structure:
# <title>
**Prepared for:** <name> · **Date:** <date>
## Executive summary (3-4 sentences)
## Key metrics (a Markdown table)
## Analysis (short sections with headings)
## Recommendations (numbered, practical)
Money is in Indian Rupees, Indian style: ₹1,24,999, ₹12.5 lakh, ₹3.2 crore.
Use ONLY the numbers in the findings. Never invent data. If something is missing, leave it out."""

mcp = MCPServer("reports", instructions="Create polished downloadable business reports.")


@mcp.tool()
def create_report(title: str, findings: str, ctx: Context) -> str:
    """Create a polished, downloadable business report. Only use when the user asks for a
    report/document. FIRST get all the data with the SQL tools, then pass EVERY number and
    insight in `findings` (the report writer can't query data itself). Returns a download
    link: give it to the user as a Markdown link."""
    user = (ctx.request_context.meta or {}).get("synapse/user") or {}
    if not user.get("email"):
        return json.dumps({"error": "Unknown user; can't create the report."})
    today = datetime.now(timezone.utc).date().isoformat()
    prompt = f"Title: {title}\nPrepared for: {user.get('name')}\nDate: {today}\n\nFindings:\n{findings}"
    try:
        r = client.converse(
            modelId=WORKER_MODEL,
            system=[{"text": WRITER_PROMPT}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 4000},
        )
    except Exception as e:
        return json.dumps({"error": f"Report writer failed: {e}"})
    # gpt-oss also returns reasoning blocks; keep only the written text
    markdown = "".join(b["text"] for b in r["output"]["message"]["content"] if "text" in b).strip()
    if not markdown:
        return json.dumps({"error": "Report writer returned nothing."})

    report_id = str(uuid.uuid4())
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / f"{report_id}.json").write_text(json.dumps({
        "id": report_id, "owner": user["email"], "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(), "markdown": markdown,
    }), encoding="utf-8")
    return json.dumps({"ok": True, "title": title, "download_url": f"/api/reports/{report_id}"})


if __name__ == "__main__":
    mcp.run()
