# MealLens backend

FastAPI service and Supabase Postgres database for MealLens.

## Database

The schema lives in `supabase/migrations/` and is applied with the Supabase CLI.

| File | Contents |
| --- | --- |
| `20261030000000_init_schema.sql` | Enums, 17 tables, indexes, `updated_at` triggers, new-user trigger |
| `20261030000100_functions.sql` | `recipe_nutrition` materialized view, `recommend_recipes()` |
| `20261030000200_rls_and_storage.sql` | Row Level Security policies, `scan-images` storage bucket |

### Apply to the hosted project

```bash
npx supabase@2.20.12 login
npx supabase@2.20.12 link --project-ref <project-ref>
npx supabase@2.20.12 db push
```

If the SQL was already run by hand in the SQL editor, mark the migrations as applied instead of pushing them again:

```bash
npx supabase@2.20.12 migration repair --status applied 20261030000000 20261030000100 20261030000200
```

### Check a database

`supabase/checks/verify_all.sql` checks a database against this repo in one query. Paste the whole file into the SQL editor and run it; every row should say PASS. It covers:

- **Structure:** each table's columns, constraints, indexes, RLS switch and policies, plus enums, functions, triggers, the storage bucket and its policy.
- **Seed data:** a fingerprint of every seed table's content.
- **Behaviour:** the sign-up trigger, recipe ranking, the allergen filter and RLS isolation between two users. These run inside a sub-transaction that is always rolled back, so nothing is left behind.

After changing a migration or the seed data, regenerate the expected values (needs a local Postgres):

```bash
PGHOST=localhost PGUSER=postgres python3 scripts/build_verify_sql.py
```

### Seed data

The ingredient catalog and the 120 recipes are written by hand in `seed_data/` (YAML). `scripts/build_seed.py` validates them and generates `supabase/seed/*.sql`:

```bash
pip install pyyaml
python3 scripts/build_seed.py          # validate and regenerate the SQL
python3 scripts/build_seed.py --check  # validate only
```

The script converts every quantity to grams, sets ingredient weights by role (core 1.0, supporting 0.5, garnish 0.2) and derives dietary tags from the ingredients. It fails if any recipe uses fewer than 2 detectable ingredients, or if any detector class appears in fewer than 8 recipes. Edit the YAML, never the generated SQL.

To load the seeds into the hosted project, run `supabase/seed/01_ingredients.sql` and then `supabase/seed/02_recipes.sql` in the SQL editor. Both are safe to re-run. `supabase db reset` loads them automatically in local development.

Nutrition values come from USDA FoodData Central SR Legacy. Each ingredient in `seed_data/ingredients.yaml` names its `fdc_id`; to refresh the values, get a free key at https://fdc.nal.usda.gov/api-key-signup and run:

```bash
FDC_API_KEY=... python3 scripts/fetch_usda_nutrition.py   # add --dry-run to preview changes
python3 scripts/build_seed.py
```

### Test locally

With any local Postgres 15+ (no Supabase needed):

```bash
PGHOST=localhost PGUSER=postgres scripts/test_schema.sh
```

This applies the migrations to a throwaway database, using stand-ins for Supabase's `auth` and `storage` schemas, then runs `supabase/tests/smoke.sql`. The last two statements of the smoke test are meant to fail with CHECK-constraint errors. It then loads the seed data into a second database and prints the row counts.

## Configuration

Copy `.env.example` to `.env` and fill it in. Use the **Session pooler** connection string from the Supabase dashboard: the direct `db.<ref>.supabase.co` host is IPv6-only.
