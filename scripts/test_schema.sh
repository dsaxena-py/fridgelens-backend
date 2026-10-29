#!/usr/bin/env bash
# Applies the migrations to a throwaway database on a plain local Postgres and runs the smoke test.
# Usage: PGHOST=localhost PGUSER=postgres scripts/test_schema.sh
set -euo pipefail
cd "$(dirname "$0")/.."
db=meallens_schema_test
psql -q -c "drop database if exists $db" -c "create database $db"
psql -q -d "$db" -f supabase/tests/supabase_stubs.sql 2>&1 | grep -v 'already exists' || true
for f in supabase/migrations/*.sql; do
  psql -q -d "$db" -v ON_ERROR_STOP=1 -f "$f"
done
psql -d "$db" -f supabase/tests/smoke.sql
