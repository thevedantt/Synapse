-- Who can see what. Enforced by Postgres itself, so the AI cannot bypass it.
--
-- The agent's SQL runs while LOGGED IN as one of these roles (picked per user).
-- They are separate logins with no way to switch to each other:
--   synapse_admin       everything
--   synapse_analyst     all regions, but no customer email column
--   synapse_mgr_<region> only customers/orders in that region (Row Level Security);
--                        the region comes from the login name, so SQL can't change it.
-- Requires: SELECT set_config('synapse.agent_password', ..., false) before running.

DO $$
DECLARE
  r text;
  pw text := current_setting('synapse.agent_password');
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'synapse_manager') THEN
    CREATE ROLE synapse_manager NOLOGIN;  -- group role holding manager grants
  END IF;
  FOREACH r IN ARRAY ARRAY['synapse_admin', 'synapse_analyst', 'synapse_mgr_north',
                           'synapse_mgr_south', 'synapse_mgr_east', 'synapse_mgr_west'] LOOP
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', r, pw);
    ELSE
      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', r, pw);
    END IF;
  END LOOP;
END $$;

GRANT synapse_manager TO synapse_mgr_north, synapse_mgr_south, synapse_mgr_east, synapse_mgr_west;
GRANT USAGE ON SCHEMA public TO synapse_admin, synapse_analyst, synapse_manager;

-- Table / column access (SELECT only: no writes even without read-only mode).
GRANT SELECT ON customers, products, orders TO synapse_admin, synapse_manager;
GRANT SELECT ON products, orders TO synapse_analyst;
GRANT SELECT (id, name, city, region, created_at) ON customers TO synapse_analyst;  -- no email

-- Row Level Security: rows filtered by the DB on every query.
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;

CREATE POLICY all_rows ON customers FOR SELECT TO synapse_admin, synapse_analyst USING (true);
CREATE POLICY own_region ON customers FOR SELECT TO synapse_manager
  USING (lower(region) = substring(current_user FROM '^synapse_mgr_(.+)$'));

CREATE POLICY all_rows ON orders FOR SELECT TO synapse_admin, synapse_analyst USING (true);
CREATE POLICY own_region ON orders FOR SELECT TO synapse_manager
  USING (customer_id IN (SELECT id FROM customers));  -- customers is already region-filtered

-- App users (demo users for now; Neon Auth login links to these later).
CREATE TABLE IF NOT EXISTS app_users (
  email text PRIMARY KEY,
  name text NOT NULL,
  role text NOT NULL CHECK (role IN ('admin', 'manager', 'analyst')),
  region text CHECK (region IN ('North', 'South', 'East', 'West')),
  CHECK (role <> 'manager' OR region IS NOT NULL)
);

INSERT INTO app_users (email, name, role, region) VALUES
  ('vedanttalekar1705@gmail.com', 'Vedant Talekar', 'admin', NULL),
  ('admin@synapse.in', 'Demo Admin', 'admin', NULL),
  ('north.manager@synapse.in', 'Rohit Malhotra (North Manager)', 'manager', 'North'),
  ('south.manager@synapse.in', 'Lakshmi Venkatesan (South Manager)', 'manager', 'South'),
  ('analyst@synapse.in', 'Sneha Iyer (Analyst)', 'analyst', NULL)
ON CONFLICT (email) DO NOTHING;

-- Audit log: every question, who asked, which tools/SQL ran, blocked or not.
CREATE TABLE IF NOT EXISTS audit_log (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  user_email text NOT NULL,
  role text NOT NULL,
  question text NOT NULL,
  tool_calls jsonb NOT NULL DEFAULT '[]',
  status text NOT NULL CHECK (status IN ('ok', 'blocked', 'error')),
  detail text
);
-- app_users and audit_log get no grants: the agent's logins cannot read them.
