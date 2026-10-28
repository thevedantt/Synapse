"""Slack MCP server: posts messages to ONE team channel via an Incoming Webhook.

The webhook URL (in .env) fixes the channel, so the agent can't send data anywhere else.
The requesting user comes from MCP request metadata (set by the API, not the LLM).
"""

import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv
from mcp.server.mcpserver import Context, MCPServer

load_dotenv()
WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
MAX_CHARS = 3000  # Slack section text limit

mcp = MCPServer("slack", instructions="Send messages to the team's Slack channel.")


@mcp.tool()
def send_slack_message(title: str, message: str, ctx: Context) -> str:
    """Post a message to the team's Slack channel. Only use this when the user explicitly
    asks to send/share/post something to Slack. Write the message in Slack markdown
    (*bold*, bullet lists with •), short and skimmable, with the key numbers."""
    if not WEBHOOK_URL:
        return json.dumps({"error": "Slack is not set up yet (SLACK_WEBHOOK_URL missing)."})
    user = (ctx.request_context.meta or {}).get("synapse/user") or {}
    sender = user.get("name") or "a Synapse user"
    payload = {
        "text": title,  # notification preview
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message[:MAX_CHARS]}},
            {"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"Sent by Synapse on behalf of {sender}"},
            ]},
        ],
    }
    req = urllib.request.Request(
        WEBHOOK_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            res.read()
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"Slack rejected the message ({e.code}): {e.read().decode()[:200]}"})
    except urllib.error.URLError as e:
        return json.dumps({"error": f"Could not reach Slack: {e.reason}"})
    return json.dumps({"ok": True, "sent_to": "team Slack channel"})


if __name__ == "__main__":
    mcp.run()
