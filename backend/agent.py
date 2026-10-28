"""Synapse agent: connects to MCP servers, discovers their tools, and lets an LLM
on Amazon Bedrock (Converse API) call them in a loop until the task is done.

Run: uv run python agent.py
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from contextlib import AsyncExitStack
from datetime import date
from pathlib import Path

import boto3
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()
log = logging.getLogger("synapse")
BACKEND_DIR = Path(__file__).parent
MODEL = os.environ.get("ORCHESTRATOR_MODEL", "deepseek.v3.2")
MAX_STEPS = 15  # hard stop so a confused loop can't burn credits

# Every MCP server the agent connects to. Add a line here to add a tool source.
SERVERS = {
    "postgres": BACKEND_DIR / "servers" / "postgres_server.py",
    "slack": BACKEND_DIR / "servers" / "slack_server.py",
    "reports": BACKEND_DIR / "servers" / "report_server.py",
    "sheets": BACKEND_DIR / "servers" / "sheets_server.py",
}

SYSTEM = """You are Synapse, an enterprise assistant for an Indian e-commerce brand.
Always respond in English.
The company is in India. All money is in Indian Rupees: write amounts Indian-style,
e.g. ₹1,24,999, and use lakh / crore for big numbers (₹12.5 lakh, ₹3.2 crore).
Use the tools to get real data; never invent numbers. The database schema is below: write
SQL from it directly (describe_table only if unsure). Aggregate in SQL, not raw rows.
Definitions: revenue = SUM(amount_inr) WHERE status = 'completed'. RTO rate = rto orders /
shipped orders that are no longer pending (courier IS NOT NULL AND status <> 'pending').
Region is on customers: JOIN customers to filter or group orders by region.
Be fast: plan every query you need first, then run them ALL in the same step (several
run_sql calls at once), or combine them into one SQL query. Avoid one-query-per-step.
Keep answers short and skimmable (under ~150 words) unless the user asks for detail.
Answer with a short summary of the findings, key numbers, and insights.
Data access depends on the user's role and is enforced by the database: if a query
returns no rows or a permission error, say the data is outside their access.
Some columns (e.g. customer email) may be hidden for their role: if a column they ask
for isn't visible, say it is restricted for their role, not that it doesn't exist.
Only send to Slack when the user explicitly asks; get the data first, then send a short
summary, then tell the user it was sent.
Only create a report when the user asks for one. Pass all the data you found to
create_report, then give the user its download_url exactly as returned (it starts with
/api/reports/, no domain) as a Markdown link: [Download report](/api/reports/...).
Only export to Google Sheets when the user asks; then share the returned url as a link."""


def system_prompt(user: dict, context: str = "") -> str:
    """System prompt + today + who is asking (so the model can explain access limits)
    + context the MCP servers publish as resources (e.g. the database schema)."""
    scope = f"{user['role']}, region {user['region']}" if user.get("region") else user["role"]
    return (f"{SYSTEM}\nToday is {date.today().isoformat()}."
            f"\nYou are talking to {user['name']} ({scope}).\n\n{context}")


def recent(messages: list, turns: int = 8) -> list:
    """Last `turns` questions (with their tool calls) so long chats stay fast and cheap.
    Cuts only at a user's text message, so tool calls and results stay paired."""
    starts = [i for i, m in enumerate(messages)
              if m["role"] == "user" and any("text" in b for b in m["content"])]
    return messages[starts[-turns]:] if len(starts) > turns else messages


async def connect_servers(stack: AsyncExitStack) -> tuple[list[dict], dict[str, ClientSession], str]:
    """Start each MCP server, ask it for its tools (dynamic discovery) and read the
    resources it publishes (context for the system prompt)."""
    tools, owner, context = [], {}, []
    for name, script in SERVERS.items():
        params = StdioServerParameters(command=sys.executable, args=[str(script)], cwd=BACKEND_DIR)
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        for t in (await session.list_tools()).tools:
            # ponytail: tool names must be unique across servers; prefix with server name if they collide
            if t.name in owner:
                raise ValueError(f"Duplicate tool {t.name!r} from server {name!r}")
            owner[t.name] = session
            # MCP tool -> Bedrock Converse toolSpec
            tools.append({"toolSpec": {
                "name": t.name,
                "description": t.description or t.name,
                "inputSchema": {"json": t.input_schema},
            }})
        if session.server_capabilities and session.server_capabilities.resources:
            for r in (await session.list_resources()).resources:
                for c in (await session.read_resource(r.uri)).contents:
                    context.append(getattr(c, "text", ""))
    return tools, owner, "\n\n".join(filter(None, context))


async def call_tool(owner: dict[str, ClientSession], tool_use: dict, user: dict) -> dict:
    """Run one tool call on the MCP server that owns it; return a Converse toolResult block.
    `user` travels as MCP metadata (not a tool argument), so the LLM can't see or change it."""
    name, args = tool_use["name"], tool_use.get("input") or {}
    try:
        meta = {"synapse/user": {k: user.get(k) for k in ("email", "role", "region", "name")}}
        result = await owner[name].call_tool(name, args, meta=meta)
        text = "\n".join(c.text for c in result.content if c.type == "text")
        text = text or json.dumps(result.structured_content)
        status = "error" if result.is_error else "success"
    except Exception as e:  # tool crash goes back to the model so it can recover
        text, status = f"Tool failed: {e}", "error"
    return {"toolResult": {"toolUseId": tool_use["toolUseId"], "content": [{"text": text}], "status": status}}


async def run_turn(client, tools: list, owner: dict, messages: list, user: dict, context: str = "",
                   gate: asyncio.Future | None = None) -> dict:
    """Agent loop for one user message. Appends to `messages` in place.
    Returns {"reply": final text, "tool_calls": [{"name", "input"}, ...]}.
    `gate`: a running safety check (guardrail). The first LLM call runs while it checks;
    nothing is executed or shown until it passes. Blocked -> {"blocked": True}."""
    tool_calls = []
    for step in range(MAX_STEPS):
        t0 = time.perf_counter()
        response = await asyncio.to_thread(
            client.converse,
            modelId=MODEL,
            system=[{"text": system_prompt(user, context)}],
            messages=recent(messages),
            toolConfig={"tools": tools},
            inferenceConfig={"maxTokens": 8000},  # DeepSeek V3.2 output cap is 8K
        )
        message = response["output"]["message"]
        messages.append(message)
        usage = response.get("usage", {})
        log.info("step %d: LLM %.1fs (%s in / %s out tokens)", step, time.perf_counter() - t0,
                 usage.get("inputTokens"), usage.get("outputTokens"))
        if gate is not None and await gate:  # blocked: run no tools, show nothing
            return {"reply": "", "tool_calls": [], "blocked": True}

        if response["stopReason"] != "tool_use":
            text = "".join(b["text"] for b in message["content"] if "text" in b)
            # Models sometimes invent a domain for our relative report links; strip it.
            text = re.sub(r"https?://[^\s)\]]*?(/api/reports/[\w-]+)", r"\1", text)
            if response["stopReason"] == "max_tokens":
                text += "\n[Answer cut off: hit max tokens.]"
            return {"reply": text, "tool_calls": tool_calls}

        calls = [b["toolUse"] for b in message["content"] if "toolUse" in b]
        tool_calls += [{"name": c["name"], "input": c.get("input")} for c in calls]
        t0 = time.perf_counter()
        results = await asyncio.gather(*(call_tool(owner, c, user) for c in calls))
        log.info("step %d: %d tool(s) %s %.1fs", step, len(calls), [c["name"] for c in calls],
                 time.perf_counter() - t0)
        # All results in ONE user message (keeps parallel tool calls working).
        messages.append({"role": "user", "content": list(results)})
    return {"reply": f"[Stopped after {MAX_STEPS} steps.]", "tool_calls": tool_calls}


CLI_USER = {"email": "admin@synapse.in", "name": "Vedant (Admin)", "role": "admin", "region": None}


async def main() -> None:
    # Auth: AWS_BEARER_TOKEN_BEDROCK (Bedrock API key) is picked up from env by boto3.
    client = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])
    async with AsyncExitStack() as stack:
        tools, owner, context = await connect_servers(stack)
        print(f"Synapse ready. Model: {MODEL}. Tools: {', '.join(owner)}")
        print("Type a question, or 'exit'.\n")
        messages: list = []
        while (question := input("you> ").strip()) not in ("exit", "quit"):
            if question:
                messages.append({"role": "user", "content": [{"text": question}]})
                result = await run_turn(client, tools, owner, messages, CLI_USER, context)
                for c in result["tool_calls"]:
                    print(f"  -> {c['name']}({json.dumps(c['input'])[:120]})")
                print(f"\nsynapse> {result['reply']}\n")


if __name__ == "__main__":
    asyncio.run(main())
