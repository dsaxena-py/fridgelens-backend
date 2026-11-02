#!/usr/bin/env bash
# Applies the migrations to a throwaway database on a plain local Postgres, runs the smoke test,
# then loads the seed data into a second throwaway database and checks the row counts.
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

seeded=meallens_seed_test
psql -q -c "drop database if exists $seeded" -c "create database $seeded"
psql -q -d "$seeded" -f supabase/tests/supabase_stubs.sql 2>&1 | grep -v 'already exists' || true
for f in supabase/migrations/*.sql supabase/seed/*.sql; do
  psql -q -d "$seeded" -v ON_ERROR_STOP=1 -f "$f"
done
psql -d "$seeded" -c "select (select count(*) from ingredients) as ingredients,
                             (select count(*) from recipes) as recipes,
                             (select count(*) from recipe_ingredients) as recipe_ingredients,
                             (select count(*) from recipe_nutrition) as recipes_with_nutrition"
