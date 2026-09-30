-- The agent connects with a role that can only read. Defence in depth: even if the SQL guard
-- were bypassed, the database itself refuses writes.
\c chinook;

CREATE ROLE sqlagent_ro LOGIN PASSWORD 'sqlagent_ro';
GRANT CONNECT ON DATABASE chinook TO sqlagent_ro;
GRANT USAGE ON SCHEMA public TO sqlagent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sqlagent_ro;
ALTER ROLE sqlagent_ro SET default_transaction_read_only = on;
ALTER ROLE sqlagent_ro SET statement_timeout = '30s';
