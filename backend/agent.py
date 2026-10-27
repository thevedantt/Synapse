"""Synapse agent: connects to MCP servers, discovers their tools, and lets an LLM
on Amazon Bedrock (Converse API) call them in a loop until the task is done.

Run: uv run python agent.py
"""

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from datetime import date
from pathlib import Path

import boto3
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()
BACKEND_DIR = Path(__file__).parent
MODEL = os.environ.get("ORCHESTRATOR_MODEL", "deepseek.v3.2")
MAX_STEPS = 15  # hard stop so a confused loop can't burn credits

# Every MCP server the agent connects to. Add a line here to add a tool source.
SERVERS = {
    "postgres": BACKEND_DIR / "servers" / "postgres_server.py",
}

SYSTEM = f"""You are Synapse, an enterprise assistant with access to company tools.
Today is {date.today().isoformat()}. Always respond in English.
The company is in India. All money is in Indian Rupees: write amounts Indian-style,
e.g. ₹1,24,999, and use lakh / crore for big numbers (₹12.5 lakh, ₹3.2 crore).
Use the tools to get real data; never invent numbers. Before writing SQL, check the
schema with list_tables / describe_table. Aggregate in SQL instead of fetching raw rows.
Answer with a short summary of the findings, key numbers, and insights.
Data access depends on the user's role and is enforced by the database: if a query
returns no rows or a permission error, say the data is outside their access.
Some columns (e.g. customer email) may be hidden for their role: if a column they ask
for isn't visible, say it is restricted for their role, not that it doesn't exist."""


def system_prompt(user: dict) -> str:
    """System prompt plus who is asking (so the model can explain access limits)."""
    scope = f"{user['role']}, region {user['region']}" if user.get("region") else user["role"]
    return f"{SYSTEM}\nYou are talking to {user['name']} ({scope})."


async def connect_servers(stack: AsyncExitStack) -> tuple[list[dict], dict[str, ClientSession]]:
    """Start each MCP server, ask it for its tools (dynamic discovery)."""
    tools, owner = [], {}
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
    return tools, owner


async def call_tool(owner: dict[str, ClientSession], tool_use: dict, user: dict) -> dict:
    """Run one tool call on the MCP server that owns it; return a Converse toolResult block.
    `user` travels as MCP metadata (not a tool argument), so the LLM can't see or change it."""
    name, args = tool_use["name"], tool_use.get("input") or {}
    try:
        meta = {"synapse/user": {"role": user["role"], "region": user.get("region")}}
        result = await owner[name].call_tool(name, args, meta=meta)
        text = "\n".join(c.text for c in result.content if c.type == "text")
        text = text or json.dumps(result.structured_content)
        status = "error" if result.is_error else "success"
    except Exception as e:  # tool crash goes back to the model so it can recover
        text, status = f"Tool failed: {e}", "error"
    return {"toolResult": {"toolUseId": tool_use["toolUseId"], "content": [{"text": text}], "status": status}}


async def run_turn(client, tools: list, owner: dict, messages: list, user: dict) -> dict:
    """Agent loop for one user message. Appends to `messages` in place.
    Returns {"reply": final text, "tool_calls": [{"name", "input"}, ...]}."""
    tool_calls = []
    for _ in range(MAX_STEPS):
        response = await asyncio.to_thread(
            client.converse,
            modelId=MODEL,
            system=[{"text": system_prompt(user)}],
            messages=messages,
            toolConfig={"tools": tools},
            inferenceConfig={"maxTokens": 8000},  # DeepSeek V3.2 output cap is 8K
        )
        message = response["output"]["message"]
        messages.append(message)

        if response["stopReason"] != "tool_use":
            text = "".join(b["text"] for b in message["content"] if "text" in b)
            if response["stopReason"] == "max_tokens":
                text += "\n[Answer cut off: hit max tokens.]"
            return {"reply": text, "tool_calls": tool_calls}

        calls = [b["toolUse"] for b in message["content"] if "toolUse" in b]
        tool_calls += [{"name": c["name"], "input": c.get("input")} for c in calls]
        results = await asyncio.gather(*(call_tool(owner, c, user) for c in calls))
        # All results in ONE user message (keeps parallel tool calls working).
        messages.append({"role": "user", "content": list(results)})
    return {"reply": f"[Stopped after {MAX_STEPS} steps.]", "tool_calls": tool_calls}


CLI_USER = {"email": "admin@synapse.in", "name": "Vedant (Admin)", "role": "admin", "region": None}


async def main() -> None:
    # Auth: AWS_BEARER_TOKEN_BEDROCK (Bedrock API key) is picked up from env by boto3.
    client = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])
    async with AsyncExitStack() as stack:
        tools, owner = await connect_servers(stack)
        print(f"Synapse ready. Model: {MODEL}. Tools: {', '.join(owner)}")
        print("Type a question, or 'exit'.\n")
        messages: list = []
        while (question := input("you> ").strip()) not in ("exit", "quit"):
            if question:
                messages.append({"role": "user", "content": [{"text": question}]})
                result = await run_turn(client, tools, owner, messages, CLI_USER)
                for c in result["tool_calls"]:
                    print(f"  -> {c['name']}({json.dumps(c['input'])[:120]})")
                print(f"\nsynapse> {result['reply']}\n")


if __name__ == "__main__":
    asyncio.run(main())
