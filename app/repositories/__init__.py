"""All SQL lives here. Every function touching user-owned tables takes `user_id` and filters on it,
because the backend's database role bypasses Row Level Security."""

INGREDIENT_REF = "jsonb_build_object('id', {a}.id, 'slug', {a}.slug, 'name', {a}.name)"
INGREDIENT = ("jsonb_build_object('id', {a}.id, 'slug', {a}.slug, 'name', {a}.name, 'category', {a}.category, "
              "'is_pantry_staple', {a}.is_pantry_staple, 'is_detectable', {a}.is_detectable, "
              "'default_unit', {a}.default_unit, 'allergens', to_jsonb({a}.allergens))")
RECIPE_SUMMARY = """jsonb_build_object(
    'id', {r}.id, 'slug', {r}.slug, 'title', {r}.title, 'description', {r}.description, 'cuisine', {r}.cuisine,
    'meal_type', {r}.meal_type, 'servings', {r}.servings, 'prep_minutes', {r}.prep_minutes,
    'cook_minutes', {r}.cook_minutes, 'difficulty', {r}.difficulty, 'dietary_tags', to_jsonb({r}.dietary_tags),
    'image_url', {r}.image_url,
    'nutrition_per_serving', case when {n}.recipe_id is null then null else jsonb_build_object(
        'kcal', {n}.kcal_per_serving, 'protein_g', {n}.protein_g_per_serving, 'carbs_g', {n}.carbs_g_per_serving,
        'fat_g', {n}.fat_g_per_serving, 'fiber_g', {n}.fiber_g_per_serving) end)"""


def ingredient_ref(alias: str) -> str:
    return INGREDIENT_REF.format(a=alias)


def ingredient(alias: str) -> str:
    return INGREDIENT.format(a=alias)


def recipe_summary(recipe_alias: str = "r", nutrition_alias: str = "n") -> str:
    return RECIPE_SUMMARY.format(r=recipe_alias, n=nutrition_alias)
