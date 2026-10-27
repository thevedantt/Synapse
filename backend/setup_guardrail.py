"""Create (or update) the Synapse Bedrock Guardrail and save its id to .env.

Run once: uv run python setup_guardrail.py
"""

import os
from pathlib import Path

import boto3
from dotenv import load_dotenv

here = Path(__file__).parent
load_dotenv(here / ".env")
NAME = "synapse-input-guard"
BLOCKED_MSG = (
    "Sorry, I can't help with that. I can answer questions about the company's "
    "sales, customers, products and reports."
)

CONFIG = dict(
    description="Screens user questions before they reach the Synapse agent.",
    blockedInputMessaging=BLOCKED_MSG,
    blockedOutputsMessaging=BLOCKED_MSG,
    contentPolicyConfig={"filtersConfig": [
        # Jailbreaks / prompt injection ("ignore your rules", "you are now admin").
        {"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"},
        {"type": "HATE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
        {"type": "INSULTS", "inputStrength": "MEDIUM", "outputStrength": "MEDIUM"},
        {"type": "SEXUAL", "inputStrength": "HIGH", "outputStrength": "HIGH"},
        {"type": "VIOLENCE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
        {"type": "MISCONDUCT", "inputStrength": "HIGH", "outputStrength": "HIGH"},
    ]},
    topicPolicyConfig={"topicsConfig": [{
        "name": "OffTopic",
        "type": "DENY",
        "definition": (
            "Requests unrelated to the company's business data, such as general trivia, "
            "writing code, jokes, stories, homework, or personal advice."
        ),
        "examples": [
            "Write me a Python script to scrape websites",
            "Tell me a joke",
            "Who won the cricket world cup?",
            "Help me with my college assignment",
        ],
    }, {
        "name": "CodingHelp",
        "type": "DENY",
        "definition": "Requests to write, fix, or explain software programs or scripts.",
        "examples": [
            "Write a Java program to sort a list",
            "Fix this JavaScript error for me",
            "Build me a web scraper",
        ],
    }]},
)

client = boto3.client("bedrock", region_name=os.environ["AWS_REGION"])
existing = [g for g in client.list_guardrails()["guardrails"] if g["name"] == NAME]
if existing:
    gid = existing[0]["id"]
    client.update_guardrail(guardrailIdentifier=gid, name=NAME, **CONFIG)
    print(f"Updated guardrail {gid}")
else:
    gid = client.create_guardrail(name=NAME, **CONFIG)["guardrailId"]
    print(f"Created guardrail {gid}")
version = client.create_guardrail_version(guardrailIdentifier=gid)["version"]
print(f"Published version {version}")

# Save id + version to .env (replace old values if present).
env = here / ".env"
lines = [l for l in env.read_text(encoding="utf-8").splitlines()
         if not l.startswith(("GUARDRAIL_ID=", "GUARDRAIL_VERSION="))]
lines += [f"GUARDRAIL_ID={gid}", f"GUARDRAIL_VERSION={version}"]
env.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("Saved GUARDRAIL_ID and GUARDRAIL_VERSION to .env")
