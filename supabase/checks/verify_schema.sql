-- Run in the Supabase SQL editor to confirm the MealLens schema is fully applied.
-- Every row should show ok = true.
with expected(kind, name) as (
  values
    ('table', 'profiles'), ('table', 'user_preferences'),
    ('table', 'ingredients'), ('table', 'ingredient_aliases'), ('table', 'ingredient_nutrition'),
    ('table', 'model_versions'), ('table', 'detector_classes'),
    ('table', 'scans'), ('table', 'detections'), ('table', 'inventory_items'),
    ('table', 'recipes'), ('table', 'recipe_ingredients'), ('table', 'recipe_steps'),
    ('table', 'saved_recipes'), ('table', 'cooking_history'),
    ('table', 'recommendation_runs'), ('table', 'recommendation_items'),
    ('matview', 'recipe_nutrition'),
    ('function', 'recommend_recipes'), ('function', 'handle_new_user'), ('function', 'set_updated_at'),
    ('trigger', 'on_auth_user_created'),
    ('bucket', 'scan-images')
)
select e.kind, e.name,
       case e.kind
         when 'table'    then exists (select 1 from pg_tables where schemaname = 'public' and tablename = e.name
                                       and rowsecurity)
         when 'matview'  then exists (select 1 from pg_matviews where schemaname = 'public' and matviewname = e.name)
         when 'function' then exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and proname = e.name)
         when 'trigger'  then exists (select 1 from pg_trigger where tgname = e.name)
         when 'bucket'   then exists (select 1 from storage.buckets where id = e.name)
       end as ok
from expected e
order by ok, e.kind, e.name;
