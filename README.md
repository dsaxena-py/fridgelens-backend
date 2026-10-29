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

Then run `supabase/checks/verify_schema.sql` in the SQL editor; every row should show `ok = true`.

### Test locally

With any local Postgres 15+ (no Supabase needed):

```bash
PGHOST=localhost PGUSER=postgres scripts/test_schema.sh
```

This applies the migrations to a throwaway database, using stand-ins for Supabase's `auth` and `storage` schemas, then runs `supabase/tests/smoke.sql`. The last two statements of the smoke test are meant to fail with CHECK-constraint errors.

## Configuration

Copy `.env.example` to `.env` and fill it in. Use the **Session pooler** connection string from the Supabase dashboard: the direct `db.<ref>.supabase.co` host is IPv6-only.
