#!/bin/sh
set -eu

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=runtime_password="$POSTGRES_RUNTIME_PASSWORD" <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sentinelai_runtime') THEN
    CREATE ROLE sentinelai_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
END
$$;
ALTER ROLE sentinelai_runtime PASSWORD :'runtime_password';
SQL
