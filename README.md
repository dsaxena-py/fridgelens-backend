# MealLens backend

FastAPI service and Supabase Postgres database for MealLens. The Flutter app signs in with Supabase
Auth and calls this API with the user's access token. The API stores scan photos, runs the
ingredient detector, keeps the virtual fridge and ranks recipes.

## API

Interactive docs are served at `/docs` once the server runs. Every `/v1` route needs
`Authorization: Bearer <Supabase access token>`.

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/health` | Database check and the loaded detector |
| GET, PATCH | `/v1/me` | Profile (display name, avatar) |
| GET, PUT | `/v1/me/preferences` | Diet tags, excluded allergens, nutrition goal, assume staples |
| GET | `/v1/ingredients?q=` | Search by name or alias ("red onion" → onion) |
| POST | `/v1/scans` | Upload a photo (multipart field `image`); returns detections to review |
| GET | `/v1/scans`, `/v1/scans/{id}` | Recent scans; one scan with boxes and a 1-hour image URL |
| POST | `/v1/scans/{id}/confirm` | Accept, reject or relabel **every** detection and add missed items, in one transaction; returns the fridge |
| GET, POST | `/v1/inventory` | The fridge (soonest expiry first); add an item by hand |
| PATCH, DELETE | `/v1/inventory/{id}` | Edit quantity, unit or expiry; remove |
| GET | `/v1/recommendations?limit=10&scan_id=` | Ranked recipes with match scores, missing ingredients and nutrition; each run is logged for evaluation |
| GET | `/v1/recipes?q=&meal_type=` | Browse recipes |
| GET | `/v1/recipes/{id}` | Ingredients (with `in_fridge`), steps, nutrition, `saved` |
| PUT, DELETE | `/v1/recipes/{id}/save` | Save or unsave |
| GET | `/v1/recipes/saved`, `/v1/recipes/history` | Saved recipes; cooking history |
| POST | `/v1/recipes/{id}/cooked` | Log a cook with an optional rating; `remove_used` takes its core ingredients out of the fridge |

How a scan works:

1. The photo's EXIF rotation is applied, it is shrunk to 1600 px and stored as JPEG at
   `scan-images/{user_id}/{scan_id}.jpg`.
2. The detector runs. Boxes are normalised to 0–1 of the stored image, and those below 0.5
   confidence come back with `needs_check: true`.
3. Nothing reaches the fridge until the user confirms. New fridge items get an expiry of today
   plus the ingredient's shelf life.

The backend connects to Postgres as the owner role, which bypasses RLS, so every query in
`app/repositories/` filters on the caller's user id. The tests check that one user cannot read or
change another's data.

## Run it

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # fill in DATABASE_URL, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
uvicorn app.main:create_app --factory --reload
```

`DETECTOR=fake` (the default) returns the fixed detections in `FAKE_DETECTIONS` for every photo,
so the Flutter app can be built before a model is trained.

## Detector model

The model is trained and exported in `fridgelens-ai`. To deploy one:

1. Copy `<name>.onnx` and `model_card.json` from the export into `models/` and commit them.
   Hosting services build from the repo; a YOLO11s model is about 40 MB.
2. Run the export's `register_model.sql` in the Supabase SQL editor, then its two activation
   statements.
3. Set `DETECTOR=onnx` and `MODEL_PATH=models/<name>.onnx`, then restart. `/health` should show
   the model name and its `model_versions` id.

Inference uses ONNX Runtime on CPU through `app/vision/onnx_infer.py`, a copy of
`fridgelens-ai/meallens_ai/onnx_infer.py`. Keep the two in sync.

## Deploy

The `Dockerfile` runs `uvicorn` on `$PORT`. Any container host works; pick one that does **not
sleep idle services**, or the first request of the demo waits for a cold start. Set the
variables from `.env.example` in the host's dashboard.

## Tests

```bash
PGHOST=localhost PGUSER=postgres pytest
```

They build a throwaway database from the migrations and seeds on a local Postgres 15+, then
exercise every endpoint with signed test tokens, in-memory storage and the fake detector.

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

All settings are environment variables; see `.env.example`. Use the **Session pooler**
connection string from the Supabase dashboard: the direct `db.<ref>.supabase.co` host is
IPv6-only. The service-role key stays on the server and must never be put in the Flutter app.
