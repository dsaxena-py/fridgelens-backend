#!/usr/bin/env python3
"""Generate supabase/checks/verify_all.sql: one query that checks a database against this repo.

The script loads the migrations and seed files into a throwaway local Postgres, measures it with
the same fingerprint query the generated file runs, and writes those measurements into the file
as the expected values. Run it again after changing a migration or the seed data.

Usage:
    PGHOST=localhost PGUSER=postgres python3 scripts/build_verify_sql.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "supabase" / "checks" / "verify_all.sql"
DB = "meallens_verify_build"

TABLES = ["profiles", "user_preferences", "ingredients", "ingredient_aliases", "ingredient_nutrition",
          "model_versions", "detector_classes", "scans", "detections", "inventory_items", "recipes",
          "recipe_ingredients", "recipe_steps", "saved_recipes", "cooking_history", "recommendation_runs",
          "recommendation_items"]

# Inventory used by the behaviour test, and what it must return (filled in from the local run).
TEST_INVENTORY = ["egg", "tomato", "onion", "bell_pepper", "cheese", "bread"]

# Every row is (check, value). The generated file runs this on the target database and
# compares each value with the one measured locally.
FINGERPRINT_SQL = r"""
select 'table ' || t.relname,
       concat_ws(' | ',
         'rls=' || t.relrowsecurity,
         'columns=' || coalesce((select md5(string_agg(format('%s:%s:%s:%s', c.column_name, c.data_type, c.is_nullable,
                                                   coalesce(c.column_default, '')), ',' order by c.ordinal_position))
                                 from information_schema.columns c
                                 where c.table_schema = 'public' and c.table_name = t.relname), '-'),
         'constraints=' || coalesce((select md5(string_agg(k.conname || '=' || pg_get_constraintdef(k.oid), ',' order by k.conname))
                                     from pg_constraint k where k.conrelid = t.oid), '-'),
         'indexes=' || coalesce((select md5(string_agg(i.indexname || '=' || i.indexdef, ',' order by i.indexname))
                                 from pg_indexes i where i.schemaname = 'public' and i.tablename = t.relname), '-'),
         'policies=' || coalesce((select md5(string_agg(concat_ws('=', p.policyname, p.cmd, p.roles::text, p.qual, p.with_check),
                                                        ',' order by p.policyname))
                                  from pg_policies p where p.schemaname = 'public' and p.tablename = t.relname), '-'))
from pg_class t
where t.relnamespace = 'public'::regnamespace and t.relkind = 'r'
union all
select 'enum types',
       md5(string_agg(ty.typname || ':' || (select string_agg(e.enumlabel, ',' order by e.enumsortorder)
                                             from pg_enum e where e.enumtypid = ty.oid), ';' order by ty.typname))
from pg_type ty where ty.typnamespace = 'public'::regnamespace and ty.typtype = 'e'
union all
select 'functions',
       string_agg(p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', ', ' order by p.proname)
from pg_proc p where p.pronamespace = 'public'::regnamespace
union all
select 'triggers',
       string_agg(tg.tgrelid::regclass::text || '.' || tg.tgname, ', ' order by tg.tgname)
from pg_trigger tg
where not tg.tgisinternal and tg.tgname in ('on_auth_user_created', 'profiles_updated', 'preferences_updated',
                                            'inventory_updated', 'recipes_updated')
union all
select 'materialized view recipe_nutrition',
       (select string_agg(a.attname || ':' || format_type(a.atttypid, a.atttypmod), ',' order by a.attnum)
        from pg_attribute a where a.attrelid = 'public.recipe_nutrition'::regclass and a.attnum > 0)
union all
select 'storage bucket scan-images',
       (select concat_ws(' | ', 'public=' || b.public, 'limit=' || b.file_size_limit, 'types=' || b.allowed_mime_types::text)
        from storage.buckets b where b.id = 'scan-images')
union all
select 'storage policy scan_images_owner',
       (select concat_ws(' | ', p.cmd, p.roles::text) from pg_policies p
        where p.schemaname = 'storage' and p.tablename = 'objects' and p.policyname = 'scan_images_owner')
union all
select 'seed ingredients',
       count(*) || ' rows, ' || md5(string_agg(concat_ws('|', slug, name, category, is_pantry_staple, is_detectable,
                                                         default_unit, grams_per_unit, shelf_life_days, allergens::text),
                                               ',' order by slug))
from ingredients
union all
select 'seed ingredient_aliases',
       count(*) || ' rows, ' || md5(string_agg(a.alias || '>' || i.slug, ',' order by a.alias))
from ingredient_aliases a join ingredients i on i.id = a.ingredient_id
union all
select 'seed ingredient_nutrition',
       count(*) || ' rows, ' || md5(string_agg(concat_ws('|', i.slug, n.kcal, n.protein_g, n.carbs_g, n.fat_g, n.fiber_g,
                                                         n.fdc_id, n.source), ',' order by i.slug))
from ingredient_nutrition n join ingredients i on i.id = n.ingredient_id
union all
select 'seed recipes',
       count(*) || ' rows, ' || md5(string_agg(concat_ws('|', slug, title, description, cuisine, meal_type, servings,
                                                         prep_minutes, cook_minutes, difficulty, dietary_tags::text,
                                                         is_published), ',' order by slug))
from recipes
union all
select 'seed recipe_ingredients',
       count(*) || ' rows, ' || md5(string_agg(concat_ws('|', r.slug, i.slug, ri.quantity, ri.unit, ri.grams, ri.role,
                                                         ri.weight, ri.note), ',' order by r.slug, i.slug))
from recipe_ingredients ri join recipes r on r.id = ri.recipe_id join ingredients i on i.id = ri.ingredient_id
union all
select 'seed recipe_steps',
       count(*) || ' rows, ' || md5(string_agg(concat_ws('|', r.slug, s.step_number, s.instruction), ','
                                               order by r.slug, s.step_number))
from recipe_steps s join recipes r on r.id = s.recipe_id
union all
select 'recipe_nutrition contents',
       count(*) || ' rows, ' || md5(string_agg(concat_ws('|', r.slug, n.kcal_per_serving, n.protein_g_per_serving,
                                                         n.carbs_g_per_serving, n.fat_g_per_serving, n.fiber_g_per_serving),
                                               ',' order by r.slug))
from recipe_nutrition n join recipes r on r.id = n.recipe_id
"""

# Behaviour checks. Runs inside a sub-transaction that is always rolled back, so it leaves
# nothing behind. Uses `x := (select ...)` rather than SELECT ... INTO because the Supabase SQL
# editor mistakes "into <name>" for a table reference.
BEHAVIOUR_SQL = r"""
create or replace function pg_temp.meallens_behaviour(expected_top3 text)
returns table (check_name text, status text, detail text)
language plpgsql as $fn$
declare
  me uuid := '00000000-0000-4000-8000-00000000a001';
  other uuid := '00000000-0000-4000-8000-00000000a002';
  names text[] := '{}';
  statuses text[] := '{}';
  details text[] := '{}';
  n integer;
  m integer;
  got text;
  blocked boolean;
begin
  -- Read through query_to_xml so a database without the Supabase CLI's history table reports a
  -- failure instead of the whole query erroring out.
  names := names || 'migration history'::text;
  if to_regclass('supabase_migrations.schema_migrations') is null then
    statuses := statuses || 'FAIL'::text;
    details := details || 'supabase_migrations.schema_migrations does not exist'::text;
  else
    got := (xpath('/row/v/text()', query_to_xml(
      'select count(*) || '' recorded: '' || coalesce(string_agg(version, '', '' order by version), ''none'') as v '
      'from supabase_migrations.schema_migrations', false, true, '')))[1]::text;
    n := (xpath('/row/v/text()', query_to_xml(
      'select count(*) as v from supabase_migrations.schema_migrations '
      'where version in (''20261030000000'', ''20261030000100'', ''20261030000200'')', false, true, '')))[1]::text::integer;
    statuses := statuses || (case when n = 3 then 'PASS' else 'FAIL' end);
    details := details || got;
  end if;

  begin
    begin
      insert into auth.users (id) values (me), (other);
    exception when others then
      names := names || 'behaviour tests'::text;
      statuses := statuses || 'SKIP'::text;
      details := details || ('could not create test users in auth.users: ' || sqlerrm);
      raise exception using errcode = 'ML001';
    end;

    n := (select count(*) from profiles p join user_preferences up on up.user_id = p.id where p.id in (me, other));
    names := names || 'sign-up trigger creates profile and preferences'::text;
    statuses := statuses || (case when n = 2 then 'PASS' else 'FAIL' end);
    details := details || (n || ' of 2 test users set up');

    insert into inventory_items (user_id, ingredient_id, source)
    select me, i.id, 'scan' from ingredients i where i.slug = any (%(inventory)s);
    insert into inventory_items (user_id, ingredient_id, source)
    select other, i.id, 'manual' from ingredients i where i.slug = 'apple';

    got := (select string_agg(r.title || ' ' || r.weighted_score, '; ' order by r.ord)
            from recommend_recipes(me, 3) with ordinality as r(recipe_id, title, match_score, weighted_score,
                                                               missing_count, missing_ingredients, ord));
    names := names || 'recommend_recipes ranking'::text;
    statuses := statuses || (case when got = expected_top3 then 'PASS' else 'FAIL' end);
    details := details || ('top 3 for ' || %(inventory_label)s || ': ' || coalesce(got, 'nothing'));

    update user_preferences set excluded_allergens = '{milk}' where user_id = me;
    n := (select count(*) from recommend_recipes(me, 200) r
          where exists (select 1 from recipe_ingredients ri join ingredients i on i.id = ri.ingredient_id
                        where ri.recipe_id = r.recipe_id and 'milk' = any (i.allergens)));
    m := (select count(*) from recommend_recipes(me, 200));
    names := names || 'allergen filter'::text;
    statuses := statuses || (case when n = 0 and m > 0 then 'PASS' else 'FAIL' end);
    details := details || (m || ' recipes returned with milk excluded, ' || n || ' contain milk');

    perform set_config('request.jwt.claims', json_build_object('sub', me, 'role', 'authenticated')::text, true);
    perform set_config('request.jwt.claim.sub', me::text, true);
    execute 'set local role authenticated';
    n := (select count(*) from inventory_items);
    m := (select count(*) from recipes);
    begin
      update ingredients set name = name where slug = 'egg';
      blocked := not found;
    exception when insufficient_privilege then
      blocked := true;
    end;
    execute 'reset role';
    names := names || 'row level security'::text;
    statuses := statuses || (case when n = %(inventory_count)s and m = %(recipe_count)s and blocked then 'PASS' else 'FAIL' end);
    details := details || ('signed-in test user sees ' || n || ' of their own %(inventory_count)s fridge items and none of the other user''s, '
                           || m || ' recipes; catalog edits ' || case when blocked then 'blocked' else 'ALLOWED' end);

    raise exception using errcode = 'ML001';
  exception
    when sqlstate 'ML001' then null;
    when others then
      names := names || 'behaviour tests'::text;
      statuses := statuses || 'FAIL'::text;
      details := details || ('error: ' || sqlerrm);
  end;
  return query select unnest(names), unnest(statuses), unnest(details);
end
$fn$;
"""


def psql(sql: str, db: str = "postgres", tuples: bool = False) -> str:
    cmd = ["psql", "-v", "ON_ERROR_STOP=1", "-q", "-d", db]
    if tuples:
        cmd += ["-At", "-F", "\t"]
    return subprocess.run(cmd, input=sql, text=True, capture_output=True, check=True).stdout


def load_reference_db() -> None:
    psql(f"drop database if exists {DB};")
    psql(f"create database {DB};")
    stubs = (ROOT / "supabase" / "tests" / "supabase_stubs.sql").read_text()
    subprocess.run(["psql", "-q", "-d", DB], input=stubs, text=True, capture_output=True)  # roles may already exist
    for f in sorted((ROOT / "supabase" / "migrations").glob("*.sql")) + sorted((ROOT / "supabase" / "seed").glob("*.sql")):
        psql(f.read_text(), DB)


def sql_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def render_behaviour(counts: dict) -> str:
    return BEHAVIOUR_SQL % {
        "inventory": "array[" + ", ".join(sql_literal(s) for s in TEST_INVENTORY) + "]",
        "inventory_label": sql_literal(", ".join(TEST_INVENTORY)),
        "inventory_count": len(TEST_INVENTORY),
        "recipe_count": counts["recipes"],
    }


def main() -> int:
    load_reference_db()
    expected = [line.split("\t", 1) for line in psql(FINGERPRINT_SQL, DB, tuples=True).splitlines() if line]
    recipe_count = int(psql("select count(*) from recipes", DB, tuples=True))
    behaviour = render_behaviour({"recipes": recipe_count})
    # First run with no expectation to learn the ranking, then confirm every check passes with it.
    detail = psql(behaviour + "select detail from pg_temp.meallens_behaviour('') "
                              "where check_name = 'recommend_recipes ranking';", DB, tuples=True).strip()
    top3 = detail.split(": ", 1)[1]
    local = psql(behaviour + f"select status || ' ' || check_name || ': ' || detail from pg_temp.meallens_behaviour({sql_literal(top3)});",
                 DB, tuples=True)
    # The reference database has no Supabase CLI history table, so that one check fails here.
    if any(not line.startswith("PASS") for line in local.splitlines() if "migration history" not in line):
        print("behaviour checks fail on the reference database:\n" + local, file=sys.stderr)
        return 1

    values = ",\n".join(f"  ({sql_literal(c)}, {sql_literal(v)})" for c, v in expected)
    out = f"""-- MealLens database check. Generated by scripts/build_verify_sql.py; do not edit by hand.
--
-- Paste the whole file into the Supabase SQL editor and run it. Every row should say PASS.
-- It compares the database with what the repo's migrations and seed files produce: table
-- structure, constraints, indexes and RLS policies, enums, functions, triggers, storage, and a
-- fingerprint of every seed table. It then runs behaviour tests (sign-up trigger, recipe ranking,
-- allergen filter, RLS) inside a sub-transaction that is always rolled back, so nothing is left
-- behind.
{behaviour}
with expected(check_name, value) as (values
{values}
),
actual(check_name, value) as (
{FINGERPRINT_SQL.strip()}
),
structural as (
  select coalesce(e.check_name, a.check_name) as check_name,
         case when e.value is not distinct from a.value then 'PASS' else 'FAIL' end as status,
         case when e.check_name is null then 'not in the repo: ' || a.value
              when a.check_name is null or a.value is null then 'missing'
              when e.value = a.value and e.check_name like 'seed %' then split_part(a.value, ',', 1) || ', content matches the seed files'
              when e.value = a.value then 'matches'
              when e.check_name like 'table %' then
                'differs in: ' || (select string_agg(split_part(x.want, '=', 1), ', ')
                                   from unnest(string_to_array(e.value, ' | '), string_to_array(a.value, ' | ')) as x(want, got)
                                   where x.want is distinct from x.got)
              when e.check_name like 'seed %' or e.check_name = 'recipe_nutrition contents' then
                'expected ' || split_part(e.value, ',', 1) || ', found ' || split_part(a.value, ',', 1)
                || '; content differs from the seed files (re-run supabase/seed/*.sql)'
              else 'expected ' || e.value || ' | found ' || a.value end as detail
  from expected e full join actual a on a.check_name = e.check_name
)
select * from (
  select * from structural
  union all select * from pg_temp.meallens_behaviour({sql_literal(top3)})
) checks(check_name, status, detail)
order by case status when 'FAIL' then 0 when 'SKIP' then 1 else 2 end, check_name;
"""
    OUT.write_text(out)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(expected)} fingerprints; expected top 3: {top3})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
