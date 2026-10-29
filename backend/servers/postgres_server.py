"""Postgres MCP server: read-only SQL access to the sales database.

Every query runs logged in as the asking user's restricted DB role (see security.sql),
so the database itself decides which rows/columns they can see. The user comes from
MCP request metadata set by the API, never from the LLM.

The schema (tables, columns, meanings from COMMENT ON) is also published as an MCP
resource, so the agent knows it up front instead of spending LLM calls discovering it.
"""

import atexit
import json
import os
import re
from decimal import Decimal

import psycopg
from dotenv import load_dotenv
from mcp.server.mcpserver import Context, MCPServer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]
AGENT_DB_PASSWORD = os.environ["AGENT_DB_PASSWORD"]
MAX_ROWS = 200
REGIONS = {"North", "South", "East", "West"}
ROLES = ["synapse_admin", "synapse_analyst"] + [f"synapse_mgr_{r.lower()}" for r in REGIONS]


def _read_only(conn: psycopg.Connection) -> None:
    conn.read_only = True  # extra layer: DB rejects writes in every transaction


# One pool per DB login: reusing open connections skips a TLS + auth handshake per query.
POOLS = {
    role: ConnectionPool(
        DATABASE_URL, min_size=1, max_size=10, configure=_read_only, open=True,
        check=ConnectionPool.check_connection,  # Neon drops idle connections: test before use
        kwargs={"user": role, "password": AGENT_DB_PASSWORD, "row_factory": dict_row, "connect_timeout": 10},
    )
    for role in ROLES
}
atexit.register(lambda: [p.close() for p in POOLS.values()])  # clean, fast shutdown

mcp = MCPServer("postgres", instructions="Read-only access to the company sales database.")


def db_role(ctx: Context) -> str:
    """Map the caller (from request metadata) to a DB login. Deny by default."""
    user = (ctx.request_context.meta or {}).get("synapse/user") or {}
    role, region = user.get("role"), user.get("region")
    if role in ("admin", "analyst"):
        return f"synapse_{role}"
    if role == "manager" and region in REGIONS:
        return f"synapse_mgr_{region.lower()}"
    raise PermissionError("No valid user context: access denied.")


def query(role: str, sql: str, params: tuple | None = None) -> list[dict]:
    # params=None: no placeholder parsing, so LLM SQL like LIKE '%x%' works as written.
    for attempt in range(2):
        with POOLS[role].connection(timeout=15) as conn:
            try:
                conn.execute("SET LOCAL statement_timeout = '10s'")
                return conn.execute(sql, params).fetchmany(MAX_ROWS + 1)
            except psycopg.OperationalError:
                # Neon closes idle connections; retry once on a fresh one (not on slow queries).
                if attempt or not conn.broken:
                    raise


MONEY = re.compile(r"amount|revenue|inr|sales|value|price|cost|margin|profit|gmv|aov|spend", re.I)


def inr(x: Decimal | int | float) -> str:
    """₹ in Indian style: ₹1,24,999 / ₹12.50 lakh / ₹3.21 crore. LLMs get this conversion wrong."""
    x = float(x)
    if abs(x) >= 1e7:
        return f"₹{x / 1e7:,.2f} crore"
    if abs(x) >= 1e5:
        return f"₹{x / 1e5:.2f} lakh"
    whole = f"{abs(round(x)):d}"
    head, tail = whole[:-3], whole[-3:]
    head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
    return f"{'-' if x < 0 else ''}₹{head + ',' if head else ''}{tail}"


def as_json(rows: list[dict]) -> str:
    # Money columns also get a ready-to-copy Indian-format string (e.g. revenue_fmt).
    for r in rows:
        for k, v in list(r.items()):
            if isinstance(v, (Decimal, int, float)) and not isinstance(v, bool) and MONEY.search(k) and not k.endswith("_pct"):
                r[f"{k}_fmt"] = inr(v)
    result = {"rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}
    return json.dumps(result, default=str, ensure_ascii=False)  # default=str handles Decimal/date


@mcp.resource("postgres://schema", name="schema", mime_type="text/plain",
              description="Tables, columns and what they mean")
def schema() -> str:
    rows = query("synapse_admin", """
        SELECT c.table_name, c.column_name, c.data_type,
               obj_description(format('public.%I', c.table_name)::regclass) AS table_note,
               col_description(format('public.%I', c.table_name)::regclass, c.ordinal_position) AS note
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
          AND c.table_name IN (SELECT table_name FROM information_schema.tables
                               WHERE table_schema = 'public' AND table_type = 'BASE TABLE')
        ORDER BY c.table_name, c.ordinal_position""")
    lines, table = [], None
    for r in rows:
        if r["table_name"] != table:
            table = r["table_name"]
            lines.append(f"\nTABLE {table}" + (f" -- {r['table_note']}" if r["table_note"] else ""))
        lines.append(f"  {r['column_name']} {r['data_type']}" + (f" -- {r['note']}" if r["note"] else ""))
    return "Database schema (PostgreSQL):" + "\n".join(lines)


@mcp.tool()
def describe_table(table: str, ctx: Context) -> str:
    """Show the columns (that you have access to) of a table. Only needed if unsure:
    the schema is already in your instructions."""
    return as_json(query(db_role(ctx),
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
        (table,)))


# Trust layer: ONE definition per business metric, so the same question always gives the
# same number. Only these whitelisted SQL fragments are ever used; filter values are params.
METRICS = {
    "revenue": "SUM(o.amount_inr) FILTER (WHERE o.status = 'completed')",
    "orders": "COUNT(*)",
    "units": "SUM(o.quantity)",
    "customers": "COUNT(DISTINCT o.customer_id)",
    "aov": "SUM(o.amount_inr) FILTER (WHERE o.status = 'completed') "
           "/ NULLIF(COUNT(*) FILTER (WHERE o.status = 'completed'), 0)",
    "gross_margin": "SUM((pr.price_inr - pr.cost_inr) * o.quantity) FILTER (WHERE o.status = 'completed')",
    "rto_rate_pct": "100.0 * COUNT(*) FILTER (WHERE o.status = 'rto') "
                    "/ NULLIF(COUNT(*) FILTER (WHERE o.courier IS NOT NULL AND o.status <> 'pending'), 0)",
    "refund_rate_pct": "100.0 * COUNT(*) FILTER (WHERE o.status = 'refunded') "
                       "/ NULLIF(COUNT(*) FILTER (WHERE o.status IN ('completed', 'refunded')), 0)",
    "cod_share_pct": "100.0 * COUNT(*) FILTER (WHERE o.payment_method = 'COD') / NULLIF(COUNT(*), 0)",
}
DIMENSIONS = {
    "region": "cu.region", "city": "cu.city", "pincode": "cu.pincode", "channel": "o.channel",
    "payment_method": "o.payment_method", "courier": "o.courier", "category": "pr.category",
    "product": "pr.name", "day": "o.order_date",
    "week": "date_trunc('week', o.order_date)::date", "month": "to_char(o.order_date, 'YYYY-MM')",
}


@mcp.tool()
def get_metrics(metrics: list[str], start_date: str, end_date: str, ctx: Context,
                group_by: list[str] | None = None, filters: dict[str, str] | None = None,
                compare_start_date: str | None = None, compare_end_date: str | None = None) -> str:
    """PREFERRED for standard numbers: verified, consistent business metrics.
    metrics: any of revenue, orders, units, customers, aov (average order value),
      gross_margin, rto_rate_pct, refund_rate_pct, cod_share_pct.
    start_date / end_date: YYYY-MM-DD, inclusive (by order date).
    group_by: any of region, city, pincode, channel, payment_method, courier, category, product,
      day, week, month.  filters: {dimension: value}, e.g. {"region": "South"}.
    compare_start_date / compare_end_date: an earlier period to compare with (e.g. last
      month). Adds <metric>_prev and <metric>_change_pct: use these, don't calculate.
    Use run_sql only for things these metrics can't express."""
    group_by, filters = group_by or [], filters or {}
    bad = [m for m in metrics if m not in METRICS] + [d for d in [*group_by, *filters] if d not in DIMENSIONS]
    if bad or not metrics:
        return json.dumps({"error": f"Unknown metric/dimension: {bad}. Metrics: {list(METRICS)}; "
                                    f"dimensions: {list(DIMENSIONS)}"})
    select = [f"{DIMENSIONS[d]} AS {d}" for d in group_by] + [f"round({METRICS[m]}, 2) AS {m}" for m in metrics]
    where = ["o.order_date BETWEEN %s::date AND %s::date"] + [f"{DIMENSIONS[d]}::text = %s" for d in filters]
    sql = (f"SELECT {', '.join(select)} FROM orders o JOIN customers cu ON cu.id = o.customer_id "
           f"JOIN products pr ON pr.id = o.product_id WHERE {' AND '.join(where)}")
    if group_by:
        sql += f" GROUP BY {', '.join(str(i + 1) for i in range(len(group_by)))}"
        sql += f" ORDER BY {', '.join(str(i + 1) for i in range(len(group_by)))}"
    try:
        rows = query(db_role(ctx), sql, (start_date, end_date, *filters.values()))
        if compare_start_date and compare_end_date:
            prev = {tuple(r[d] for d in group_by): r for r in
                    query(db_role(ctx), sql, (compare_start_date, compare_end_date, *filters.values()))}
            for r in rows:
                p = prev.get(tuple(r[d] for d in group_by), {})
                for m in metrics:
                    r[f"{m}_prev"] = p.get(m)
                    if r[m] is not None and p.get(m):
                        r[f"{m}_change_pct"] = round((r[m] - p[m]) / p[m] * 100, 1)
        return as_json(rows)
    except psycopg.Error as e:
        return json.dumps({"error": str(e).strip()})


@mcp.tool()
def run_sql(sql: str, ctx: Context) -> str:
    """Run a read-only SQL query (SELECT only). Returns up to 200 rows as JSON.
    Prefer aggregating in SQL (SUM, GROUP BY) over fetching raw rows.
    Results only include data the current user is allowed to see."""
    try:
        return as_json(query(db_role(ctx), sql))
    except psycopg.Error as e:  # includes permission errors, connection errors, timeouts
        # Return the error to the LLM so it can fix its SQL and retry.
        return json.dumps({"error": str(e).strip()})


if __name__ == "__main__":
    mcp.run()
