"""Ops MCP server: RTO Shield (risky COD orders), Stock Sense (festival-aware stock
forecast) and action proposals for the Action Center.

Reads run as the asking user's restricted DB role (same pools as the postgres server), so
managers only score / forecast their own region. Proposals are written by this trusted
code with the owner login; nothing is executed until a person approves it.
"""

import json
import os

import psycopg
from dotenv import load_dotenv
from mcp.server.mcpserver import Context, MCPServer
from psycopg.types.json import Jsonb

from postgres_server import as_json, db_role, query

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]
# Factors aren't independent, so their product overshoots. Backtest (orders 8-21 days old,
# scored on the 90 days before): 0.5 gives avg calibration error 0.9 pts; top 10% riskiest
# orders were RTO 53% of the time vs 28% average.
DAMPING = 0.5
SMOOTHING = 20  # pseudo-orders at the base rate, so a pincode with 3 orders isn't "100% RTO"

mcp = MCPServer("ops", instructions="RTO risk, stock forecasts and action proposals.")


def rto_sql(targets: str, history: str) -> str:
    """Risk = base COD RTO rate x lift of each factor (pincode, courier, category, new vs
    repeat customer, customer's own history), learned from settled COD orders in `history`.
    Factors multiply (naive-Bayes style), so the result is capped."""
    lift = lambda t: f"coalesce(({t}.r + {SMOOTHING} * b.rate) / ({t}.n + {SMOOTHING}) / b.rate, 1)"
    return f"""
    WITH h AS (
      SELECT o.customer_id, o.courier, o.order_date, (o.status = 'rto')::int AS rto,
             cu.pincode, pr.category, (o.order_date - cu.created_at < 30) AS is_new
      FROM orders o JOIN customers cu ON cu.id = o.customer_id JOIN products pr ON pr.id = o.product_id
      WHERE o.payment_method = 'COD' AND o.status IN ('completed', 'refunded', 'rto') AND {history}
    ),
    b AS (SELECT avg(rto) AS rate FROM h),
    pin AS (SELECT pincode AS k, count(*) AS n, sum(rto) AS r FROM h GROUP BY 1),
    cour AS (SELECT courier AS k, count(*) AS n, sum(rto) AS r FROM h GROUP BY 1),
    cat AS (SELECT category AS k, count(*) AS n, sum(rto) AS r FROM h GROUP BY 1),
    nw AS (SELECT is_new AS k, count(*) AS n, sum(rto) AS r FROM h GROUP BY 1),
    cust AS (SELECT customer_id AS k, count(*) AS n, sum(rto) AS r FROM h GROUP BY 1)
    SELECT o.id AS order_id, o.order_date, o.status, cu.name AS customer, cu.city, cu.region, cu.pincode,
           pr.name AS product, o.amount_inr, o.courier, o.channel,
           round(least(0.95, b.rate * power(f.pin * f.courier * f.category * f.new_customer * f.history, {DAMPING})) * 100, 1)
             AS risk_pct,
           round(b.rate * 100, 1) AS base_rate_pct,
           round(f.pin, 2) AS pincode_lift, round(f.courier, 2) AS courier_lift,
           round(f.category, 2) AS category_lift, round(f.new_customer, 2) AS new_customer_lift,
           round(f.history, 2) AS history_lift, coalesce(cust.n, 0) AS past_cod_orders,
           coalesce(cust.r, 0) AS past_rtos
    FROM orders o
    JOIN customers cu ON cu.id = o.customer_id
    JOIN products pr ON pr.id = o.product_id
    CROSS JOIN b
    LEFT JOIN pin ON pin.k = cu.pincode
    LEFT JOIN cour ON cour.k = o.courier
    LEFT JOIN cat ON cat.k = pr.category
    LEFT JOIN nw ON nw.k = (o.order_date - cu.created_at < 30)
    LEFT JOIN cust ON cust.k = o.customer_id
    CROSS JOIN LATERAL (SELECT {lift('pin')} AS pin, {lift('cour')} AS courier, {lift('cat')} AS category,
                               {lift('nw')} AS new_customer, {lift('cust')} AS history) f
    WHERE o.payment_method = 'COD' AND o.courier IS NOT NULL AND {targets}
    ORDER BY risk_pct DESC
    """


def reasons(r: dict) -> list[str]:
    """Plain-English 'why' for a risk score: factors that raise risk by 30%+."""
    out = []
    if r["pincode_lift"] >= 1.3:
        out.append(f"pincode {r['pincode']} has {r['pincode_lift']}x the usual COD return rate")
    if r["courier_lift"] >= 1.1:
        out.append(f"{r['courier']} returns {r['courier_lift']}x more COD orders than average")
    if r["history_lift"] >= 1.3:
        out.append(f"customer returned {r['past_rtos']} of {r['past_cod_orders']} past COD orders")
    if r["new_customer_lift"] >= 1.1:
        out.append("new customer (joined in the last 30 days)")
    if r["category_lift"] >= 1.1:
        out.append(f"{r['product']}'s category returns {r['category_lift']}x more")
    return out or ["no single strong factor; overall risk from several small ones"]


@mcp.tool()
def score_cod_risk(ctx: Context, limit: int = 20, min_risk_pct: float = 0) -> str:
    """RTO Shield: score every PENDING cash-on-delivery order for RTO risk (chance it comes
    back undelivered), riskiest first, with the reasons. Learned from the last 90 days of
    delivered/returned COD orders. Suggested action for high risk: confirm on WhatsApp and
    offer a prepaid payment link (propose it with propose_action, kind cod_confirmation)."""
    sql = rto_sql("o.status = 'pending'", "o.order_date >= current_date - 90")
    try:
        rows = query(db_role(ctx), f"SELECT * FROM ({sql}) s WHERE risk_pct >= %s LIMIT %s",
                     (min_risk_pct, min(limit, 100)))
    except psycopg.Error as e:
        return json.dumps({"error": str(e).strip()})
    for r in rows:
        r["level"] = "high" if r["risk_pct"] >= 40 else "medium" if r["risk_pct"] >= 25 else "low"
        r["reasons"] = reasons(r)
    return as_json(rows)


@mcp.tool()
def stock_forecast(ctx: Context, horizon_days: int = 21, only_at_risk: bool = True, limit: int = 50) -> str:
    """Stock Sense: forecast demand per product and regional warehouse for the next
    horizon_days, using the last 28 days of sales (festival effects removed) times the
    festival boost of each coming day (e.g. Diwali). Returns days of stock left, the date
    it runs out, and how much to reorder (to cover lead time + 2 weeks).
    status: out_of_stock / critical (runs out before a restock could arrive) / low / ok.
    To restock, propose it with propose_action (kind restock)."""
    sql = """
    WITH boost AS (
      SELECT d::date AS d, coalesce(max(f.boost), 1) AS boost
      FROM generate_series(current_date - 28, current_date + 120, interval '1 day') d  -- covers lead time too
      LEFT JOIN festivals f ON d::date BETWEEN f.start_date AND f.end_date
      GROUP BY 1
    ),
    base AS (  -- normal daily demand: past sales divided by that day's festival boost
      SELECT o.product_id, cu.region, sum(o.quantity / b.boost) / 28.0 AS daily
      FROM orders o JOIN customers cu ON cu.id = o.customer_id JOIN boost b ON b.d = o.order_date
      WHERE o.order_date >= current_date - 28 AND o.order_date < current_date
      GROUP BY 1, 2
    ),
    fc AS (
      SELECT i.product_id, i.region, i.on_hand, i.reorder_point, i.lead_time_days, bs.daily,
             fd.d, sum(bs.daily * fd.boost) OVER (PARTITION BY i.product_id, i.region ORDER BY fd.d) AS cum
      FROM inventory i
      JOIN base bs ON bs.product_id = i.product_id AND bs.region = i.region
      JOIN boost fd ON fd.d >= current_date
    ),
    s AS (
      SELECT product_id, region, on_hand, lead_time_days, round(daily, 1) AS normal_daily_demand,
             round(max(cum) FILTER (WHERE d < current_date + %(h)s)) AS forecast_units,
             min(d) FILTER (WHERE cum >= on_hand) AS runs_out_on,
             ceil(max(cum) FILTER (WHERE d < current_date + lead_time_days + 14)) AS need_for_cover
      FROM fc GROUP BY product_id, region, on_hand, lead_time_days, daily
    )
    SELECT pr.name AS product, s.region, s.on_hand, s.normal_daily_demand, s.forecast_units,
           s.runs_out_on, s.runs_out_on - current_date AS days_left, s.lead_time_days,
           greatest(0, s.need_for_cover - s.on_hand)::int AS reorder_qty,
           CASE WHEN s.on_hand = 0 THEN 'out_of_stock'
                WHEN s.runs_out_on < current_date + s.lead_time_days THEN 'critical'
                WHEN s.runs_out_on < current_date + s.lead_time_days + 7 THEN 'low'
                ELSE 'ok' END AS status,
           (SELECT string_agg(name, ', ') FROM festivals
             WHERE start_date <= current_date + %(h)s AND end_date >= current_date) AS festivals_ahead
    FROM s JOIN products pr ON pr.id = s.product_id
    """
    try:
        rows = query(db_role(ctx), f"SELECT * FROM ({sql}) x WHERE NOT %(r)s OR status <> 'ok' "
                     "ORDER BY days_left NULLS LAST, product LIMIT %(n)s",
                     {"h": min(max(horizon_days, 1), 90), "r": only_at_risk, "n": min(limit, 200)})
    except psycopg.Error as e:
        return json.dumps({"error": str(e).strip()})
    return as_json(rows)


@mcp.tool()
def propose_action(kind: str, title: str, reason: str, payload: dict, ctx: Context) -> str:
    """Propose an action for a person to approve in the Action Center (nothing runs now).
    kind "cod_confirmation": payload {"order_ids": [..]} -> WhatsApp confirmation + prepaid
      link with a small discount for risky COD orders.
    kind "restock": payload {"items": [{"product": .., "region": .., "qty": ..}]} -> email
      the supplier a purchase request.
    reason: the numbers behind it (e.g. "risk 62%: pincode 3x usual RTO"), shown to the approver."""
    user = (ctx.request_context.meta or {}).get("synapse/user") or {}
    db_role(ctx)  # deny if there's no valid user
    if kind not in ("cod_confirmation", "restock"):
        return json.dumps({"error": "kind must be cod_confirmation or restock"})
    with psycopg.connect(DATABASE_URL, connect_timeout=10) as conn:
        action_id = conn.execute(
            "INSERT INTO actions (created_by, role, region, kind, title, reason, payload) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (user.get("email"), user.get("role"), user.get("region"), kind, title[:200], reason[:2000],
             Jsonb(payload))).fetchone()[0]
    return json.dumps({"ok": True, "action_id": action_id, "status": "proposed",
                       "note": "Waiting for approval in the Action Center."})


if __name__ == "__main__":
    mcp.run()
