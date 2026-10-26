"""Synapse agent: connects to MCP servers, discovers their tools, and lets Claude
(on Amazon Bedrock) call them in a loop until the task is done.

Run: uv run python agent.py
"""

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from datetime import date
from pathlib import Path

from anthropic import (
    AsyncAnthropicBedrockMantle,
    BetaFallbackState,
    BetaRefusalFallbackMiddleware,
)
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()
BACKEND_DIR = Path(__file__).parent
MODEL = os.environ.get("BEDROCK_MODEL", "anthropic.claude-opus-5-5")
MAX_STEPS = 15  # hard stop so a confused loop can't burn credits

# Every MCP server the agent connects to. Add a line here to add a tool source.
SERVERS = {
    "postgres": BACKEND_DIR / "servers" / "postgres_server.py",
}

SYSTEM = f"""You are Synapse, an enterprise assistant with access to company tools.
Today is {date.today().isoformat()}.
Use the tools to get real data; never invent numbers. Before writing SQL, check the
schema with list_tables / describe_table. Aggregate in SQL instead of fetching raw rows.
Answer with a short summary of the findings, key numbers, and insights."""


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
            tools.append({"name": t.name, "description": t.description or "", "input_schema": t.input_schema})
    return tools, owner


async def call_tool(owner: dict[str, ClientSession], name: str, args: dict) -> dict:
    """Run one tool call on the MCP server that owns it; return a tool_result body."""
    try:
        result = await owner[name].call_tool(name, args)
        text = "\n".join(c.text for c in result.content if c.type == "text")
        return {"content": text or json.dumps(result.structured_content), "is_error": bool(result.is_error)}
    except Exception as e:  # tool crash goes back to Claude so it can recover
        return {"content": f"Tool failed: {e}", "is_error": True}


async def run_turn(client, tools, owner, messages: list) -> str:
    """Agent loop for one user message. Appends to `messages` in place (append-only)."""
    for _ in range(MAX_STEPS):
        response = await client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=tools,
            messages=messages,
            output_config={"effort": "medium"},
            cache_control={"type": "ephemeral"},  # reuse cached prefix: cheaper repeat calls
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            return "[Claude declined this request.]"
        if response.stop_reason != "tool_use":
            text = "".join(b.text for b in response.content if b.type == "text")
            if response.stop_reason == "max_tokens":
                text += "\n[Answer cut off: hit max_tokens.]"
            return text

        calls = [b for b in response.content if b.type == "tool_use"]
        results = await asyncio.gather(*(call_tool(owner, c.name, c.input) for c in calls))
        for c in calls:
            print(f"  -> {c.name}({json.dumps(c.input)[:120]})")
        # All results in ONE user message (keeps parallel tool calls working).
        messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": c.id, **r} for c, r in zip(calls, results)],
        })
    return f"[Stopped after {MAX_STEPS} steps.]"


async def main() -> None:
    client = AsyncAnthropicBedrockMantle(
        aws_region=os.environ["AWS_REGION"],
        # If the main model refuses, retry once on an older Opus.
        middleware=[BetaRefusalFallbackMiddleware([{"model": "anthropic.claude-opus-4-8"}])],
    )
    async with AsyncExitStack() as stack:
        tools, owner = await connect_servers(stack)
        print(f"Synapse ready. Model: {MODEL}. Tools: {', '.join(owner)}")
        print("Type a question, or 'exit'.\n")
        messages: list = []
        with BetaFallbackState():  # keep the conversation on whichever model accepted
            while (question := input("you> ").strip()) not in ("exit", "quit"):
                if not question:
                    continue
                messages.append({"role": "user", "content": question})
                print(f"\nsynapse> {await run_turn(client, tools, owner, messages)}\n")


if __name__ == "__main__":
    asyncio.run(main())
