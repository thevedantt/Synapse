"""Synapse HTTP API: lets the frontend chat with the agent.

Run: uv run uvicorn api:app --reload --port 8000
Docs: http://localhost:8000/docs
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Literal
from urllib.parse import urlparse
from contextlib import AsyncExitStack, asynccontextmanager

import boto3
import jwt
import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, EmailStr, Field, model_validator

from agent import BACKEND_DIR, MODEL, connect_servers, run_turn

REPORTS_DIR = BACKEND_DIR / "reports"  # written by servers/report_server.py

log = logging.getLogger("synapse")
DATABASE_URL = os.environ["DATABASE_URL"]
NEON_AUTH_BASE_URL = os.environ["NEON_AUTH_BASE_URL"]
AUTH_ORIGIN = "{0.scheme}://{0.netloc}".format(urlparse(NEON_AUTH_BASE_URL))  # token iss/aud
jwks = jwt.PyJWKClient(f"{NEON_AUTH_BASE_URL}/.well-known/jwks.json")  # caches keys
# Dev only: lets fake demo accounts (e.g. north.manager@synapse.in) sign in. Never in production.
ALLOW_UNVERIFIED_EMAILS = os.environ.get("ALLOW_UNVERIFIED_EMAILS") == "true"
GUARDRAIL_ID = os.environ["GUARDRAIL_ID"]  # created by setup_guardrail.py
GUARDRAIL_VERSION = os.environ["GUARDRAIL_VERSION"]
BLOCKED_REPLY = (
    "Sorry, I can't help with that. I can answer questions about the company's "
    "sales, customers, products and reports."
)
state: dict = {}
# ponytail: chat history lives in memory, lost on restart; move to a Postgres table when needed
sessions: dict[str, dict] = {}  # session_id -> {"email": owner, "messages": [...]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the MCP servers once at boot, shared by every request.
    async with AsyncExitStack() as stack:
        state["tools"], state["owner"] = await connect_servers(stack)
        state["client"] = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])
        yield


app = FastAPI(title="Synapse", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # the Next.js frontend
    allow_methods=["*"],
    allow_headers=["*"],
)


def db(sql: str, params: tuple = ()) -> list[dict]:
    """App-level queries (users, audit log) with the owner connection, not the agent's."""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


def verify_token(token: str) -> dict:
    """Check the Neon Auth JWT: signature (EdDSA, from JWKS), expiry, issuer, audience."""
    key = jwks.get_signing_key_from_jwt(token).key
    return jwt.decode(
        token, key, algorithms=["EdDSA"], issuer=AUTH_ORIGIN, audience=AUTH_ORIGIN,
        leeway=30,  # tolerate small clock differences between Neon and this machine
    )


async def current_user(
    creds: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
) -> dict:
    """Signed-in user from the Bearer token, mapped to their Synapse role in app_users."""
    try:
        claims = await asyncio.to_thread(verify_token, creds.credentials)
    except jwt.PyJWTError as e:
        log.warning("Rejected login token: %s: %s", type(e).__name__, e)
        raise HTTPException(401, "Invalid or expired login") from e
    email = (claims.get("email") or "").lower()
    # Unverified email = anyone could have typed it. Google logins are verified.
    if not claims.get("emailVerified") and not ALLOW_UNVERIFIED_EMAILS:
        raise HTTPException(403, "Please verify your email first.")
    rows = await asyncio.to_thread(
        db, "SELECT email, name, role, region FROM app_users WHERE lower(email) = %s", (email,)
    )
    if not rows:
        raise HTTPException(403, f"{email} has no Synapse role yet. Ask an admin to add you.")
    return {**rows[0], "image": claims.get("image")}  # profile photo (e.g. Google)


def check_guardrail(text: str) -> str | None:
    """Screen the question with the Bedrock Guardrail. Returns the block reason, or None."""
    r = state["client"].apply_guardrail(
        guardrailIdentifier=GUARDRAIL_ID, guardrailVersion=GUARDRAIL_VERSION,
        source="INPUT", content=[{"text": {"text": text}}],
    )
    if r["action"] != "GUARDRAIL_INTERVENED":
        return None
    reasons = []
    for a in r.get("assessments", []):
        reasons += [f["type"] for f in a.get("contentPolicy", {}).get("filters", []) if f.get("action") == "BLOCKED"]
        reasons += [t["name"] for t in a.get("topicPolicy", {}).get("topics", []) if t.get("action") == "BLOCKED"]
    return ", ".join(reasons) or "guardrail"


def audit(user: dict, question: str, tool_calls: list, status: str, detail: str | None = None):
    db(
        "INSERT INTO audit_log (user_email, role, question, tool_calls, status, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user["email"], user["role"], question, Jsonb(tool_calls), status, detail),
    )


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None  # omit to start a new conversation


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[dict]
    blocked: bool = False


@app.get("/me")
def me(user: dict = Depends(current_user)):
    return user


@app.get("/tools")
def list_tools(user: dict = Depends(current_user)):
    """Tools discovered from the MCP servers."""
    return {"model": MODEL, "tools": [
        {"name": t["toolSpec"]["name"], "description": t["toolSpec"]["description"]}
        for t in state["tools"]
    ]}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user: dict = Depends(current_user)):
    session_id = req.session_id or str(uuid.uuid4())
    session = sessions.setdefault(session_id, {"email": user["email"], "messages": []})
    if session["email"] != user["email"]:
        raise HTTPException(403, "This conversation belongs to another user")
    # ponytail: input-only guardrail; add a source="OUTPUT" check on the reply if needed
    reason = await asyncio.to_thread(check_guardrail, req.message)
    if reason:
        await asyncio.to_thread(audit, user, req.message, [], "blocked", reason)
        return ChatResponse(session_id=session_id, reply=BLOCKED_REPLY, tool_calls=[], blocked=True)

    messages = session["messages"]
    start = len(messages)
    messages.append({"role": "user", "content": [{"text": req.message}]})
    try:
        result = await run_turn(state["client"], state["tools"], state["owner"], messages, user)
    except Exception as e:
        del messages[start:]  # roll back this turn so the history stays valid
        await asyncio.to_thread(audit, user, req.message, [], "error", str(e)[:500])
        log.exception("Agent turn failed")  # full error in server log
        raise HTTPException(502, "Sorry, I couldn't answer that right now. Please try again.") from e
    await asyncio.to_thread(audit, user, req.message, result["tool_calls"], "ok")
    return ChatResponse(session_id=session_id, **result)


@app.get("/reports/{report_id}")
def get_report(report_id: uuid.UUID, user: dict = Depends(current_user)):
    """A report made by create_report. Only its owner (or an admin) can download it."""
    path = REPORTS_DIR / f"{report_id}.json"  # UUID type blocks path tricks like ../
    if not path.exists():
        raise HTTPException(404, "Report not found")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["owner"].lower() != user["email"].lower() and user["role"] != "admin":
        raise HTTPException(404, "Report not found")  # don't reveal it exists
    return report


# ---------- Admin (admins only, enforced here on the server) ----------

def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Admins only")
    return user


class UserIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "manager", "analyst"]
    region: Literal["North", "South", "East", "West"] | None = None

    @model_validator(mode="after")
    def manager_needs_region(self):
        if self.role == "manager" and not self.region:
            raise ValueError("Managers need a region")
        if self.role != "manager":
            self.region = None  # region only means something for managers
        return self


@app.get("/admin/users")
async def admin_list_users(admin: dict = Depends(require_admin)):
    # Photo comes from the person's login account (e.g. Google), if they've signed in.
    return await asyncio.to_thread(
        db,
        "SELECT a.email, a.name, a.role, a.region, u.image FROM app_users a "
        'LEFT JOIN neon_auth."user" u ON lower(u.email) = lower(a.email) '
        "ORDER BY a.role, a.email",
    )


@app.put("/admin/users")
async def admin_upsert_user(body: UserIn, admin: dict = Depends(require_admin)):
    """Add a user, or update an existing one's name/role/region."""
    email = body.email.lower()
    if email == admin["email"].lower() and body.role != "admin":
        raise HTTPException(400, "You can't remove your own admin role")
    await asyncio.to_thread(
        db,
        "INSERT INTO app_users (email, name, role, region) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name, role = EXCLUDED.role, "
        "region = EXCLUDED.region",
        (email, body.name, body.role, body.region),
    )
    return {"ok": True}


@app.delete("/admin/users/{email}")
async def admin_delete_user(email: str, admin: dict = Depends(require_admin)):
    if email.lower() == admin["email"].lower():
        raise HTTPException(400, "You can't delete yourself")
    await asyncio.to_thread(db, "DELETE FROM app_users WHERE lower(email) = lower(%s)", (email,))
    return {"ok": True}


@app.get("/admin/audit")
async def admin_audit(limit: int = 50, admin: dict = Depends(require_admin)):
    return await asyncio.to_thread(
        db,
        "SELECT id, at, user_email, role, question, tool_calls, status, detail "
        "FROM audit_log ORDER BY id DESC LIMIT %s",
        (min(max(limit, 1), 500),),
    )
