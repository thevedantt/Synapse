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
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
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
# App queries (users, audit, chats) reuse open connections instead of a new one each time.
pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False, check=ConnectionPool.check_connection,
                      kwargs={"row_factory": dict_row, "connect_timeout": 10})


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the MCP servers once at boot, shared by every request.
    async with AsyncExitStack() as stack:
        state["tools"], state["owner"], state["context"] = await connect_servers(stack)
        state["client"] = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])
        pool.open()
        yield
        pool.close()


app = FastAPI(title="Synapse", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # the Next.js frontend
    allow_methods=["*"],
    allow_headers=["*"],
)


def db(sql: str, params: tuple = ()) -> list[dict]:
    """App-level queries (users, audit log, chats) with the owner login, not the agent's."""
    for attempt in range(2):
        with pool.connection(timeout=15) as conn:
            try:
                cur = conn.execute(sql, params)
                return cur.fetchall() if cur.description else []
            except psycopg.OperationalError:
                if attempt or not conn.broken:  # retry once only if Neon dropped the connection
                    raise


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
    session_id: uuid.UUID | None = None  # omit to start a new conversation


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
async def chat(req: ChatRequest, background: BackgroundTasks, user: dict = Depends(current_user)):
    # Chats live in Postgres: survive restarts, and any API worker can continue them.
    messages, turns = [], []
    if req.session_id:
        rows = await asyncio.to_thread(
            db, "SELECT messages, turns FROM chats WHERE id = %s AND user_email = %s",
            (req.session_id, user["email"]))
        if not rows:
            raise HTTPException(404, "Chat not found")
        messages, turns = rows[0]["messages"], rows[0]["turns"]
    session_id = str(req.session_id or uuid.uuid4())

    # Guardrail runs in parallel with the first LLM call (saves ~1s); run_turn waits for it
    # before running any tool or showing anything.
    # ponytail: input-only guardrail; add a source="OUTPUT" check on the reply if needed
    guard = asyncio.create_task(asyncio.to_thread(check_guardrail, req.message))
    start = len(messages)
    messages.append({"role": "user", "content": [{"text": req.message}]})
    try:
        result = await run_turn(state["client"], state["tools"], state["owner"], messages, user,
                                state["context"], gate=guard)
    except Exception as e:  # nothing saved, so the stored history stays valid
        guard.cancel()
        background.add_task(audit, user, req.message, [], "error", str(e)[:500])
        log.exception("Agent turn failed")  # full error in server log
        raise HTTPException(502, "Sorry, I couldn't answer that right now. Please try again.") from e
    if result.get("blocked"):
        del messages[start:]  # blocked questions never reach the model's history
        background.add_task(audit, user, req.message, [], "blocked", guard.result())
        result = {"reply": BLOCKED_REPLY, "tool_calls": [], "blocked": True}
    else:
        background.add_task(audit, user, req.message, result["tool_calls"], "ok")
    turns.append({"question": req.message, **result})
    # Saved after the reply is sent (the user doesn't wait for the slow DB round trip).
    # ponytail: a follow-up sent within ~1s could miss this save; fine for human typing speed
    background.add_task(save_chat, session_id, user["email"], messages, turns)
    return ChatResponse(session_id=session_id, **result)


def save_chat(chat_id: str, email: str, messages: list, turns: list) -> None:
    db("INSERT INTO chats (id, user_email, title, messages, turns) VALUES (%s, %s, %s, %s, %s) "
       "ON CONFLICT (id) DO UPDATE SET messages = EXCLUDED.messages, turns = EXCLUDED.turns, "
       "updated_at = now()",
       (chat_id, email, turns[0]["question"][:80], Jsonb(messages), Jsonb(turns)))


@app.get("/chats")
async def list_chats(user: dict = Depends(current_user)):
    """The user's recent conversations, newest first (for the sidebar)."""
    return await asyncio.to_thread(
        db, "SELECT id, title, updated_at FROM chats WHERE user_email = %s "
            "ORDER BY updated_at DESC LIMIT 50", (user["email"],))


@app.get("/chats/{chat_id}")
async def get_chat(chat_id: uuid.UUID, user: dict = Depends(current_user)):
    rows = await asyncio.to_thread(
        db, "SELECT id, title, turns FROM chats WHERE id = %s AND user_email = %s",
        (chat_id, user["email"]))
    if not rows:
        raise HTTPException(404, "Chat not found")
    return rows[0]


@app.delete("/chats/{chat_id}")
async def delete_chat(chat_id: uuid.UUID, user: dict = Depends(current_user)):
    await asyncio.to_thread(
        db, "DELETE FROM chats WHERE id = %s AND user_email = %s", (chat_id, user["email"]))
    return {"ok": True}


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
