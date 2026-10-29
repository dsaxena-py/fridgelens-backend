#!/usr/bin/env python3
"""Validate seed_data/*.yaml and generate supabase/seed/*.sql.

Usage:
    python3 scripts/build_seed.py           # validate, write SQL, print coverage stats
    python3 scripts/build_seed.py --check   # validate only; exit 1 on any problem

Recipe ingredient lines look like:
    "2 piece egg"
    "0.5 piece onion | supporting | finely chopped"
    "1 tbsp parsley | garnish"
i.e. "<quantity> <unit> <ingredient slug>", then optional "| role" and "| note".
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "seed_data"
OUT = ROOT / "supabase" / "seed"

CATEGORIES = {"produce", "dairy", "protein", "bakery", "grain", "pantry", "spice", "condiment", "other"}
ALLERGENS = {"milk", "egg", "gluten", "soy", "peanut", "tree_nut", "fish", "shellfish", "sesame"}
UNITS = {"g", "kg", "ml", "l", "piece", "tsp", "tbsp", "cup", "clove", "slice", "pinch"}
MEALS = {"breakfast", "lunch", "dinner", "snack", "dessert"}
DIFFICULTIES = {"easy", "medium", "hard"}
ROLE_WEIGHTS = {"core": 1.0, "supporting": 0.5, "garnish": 0.2}
ML_PER = {"ml": 1.0, "l": 1000.0, "tsp": 4.93, "tbsp": 14.79, "cup": 236.6}
COUNT_UNITS = {"piece", "clove", "slice"}
PINCH_G = 0.4
NUTRIENTS = ("kcal", "protein_g", "carbs_g", "fat_g", "fiber_g")

# Recipe-level targets from the design doc.
MIN_DETECTABLE_PER_RECIPE = 2
MIN_RECIPES_PER_CLASS = 8
HIGH_PROTEIN_G = 20
LOW_CARB_G = 15
NUTRITION_SOURCE = "USDA FoodData Central SR Legacy"


class SeedError(Exception):
    pass


def q(value) -> str:
    """SQL literal."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(round(value, 2)) if isinstance(value, float) else str(value)
    if isinstance(value, (list, tuple)):
        return "'{" + ",".join(value) + "}'"
    return "'" + str(value).replace("'", "''") + "'"


def load_ingredients(errors: list[str]) -> dict[str, dict]:
    rows = yaml.safe_load((DATA / "ingredients.yaml").read_text())
    by_slug: dict[str, dict] = {}
    classes: dict[int, str] = {}
    aliases: dict[str, str] = {}
    for row in rows:
        slug = row["slug"]
        where = f"ingredient {slug}"
        if slug in by_slug:
            errors.append(f"{where}: duplicate slug")
        if row["category"] not in CATEGORIES:
            errors.append(f"{where}: unknown category {row['category']}")
        if row.get("unit", "g") not in UNITS:
            errors.append(f"{where}: unknown unit {row.get('unit')}")
        for a in row.get("allergens", []):
            if a not in ALLERGENS:
                errors.append(f"{where}: unknown allergen {a}")
        if len(row.get("nutrition", [])) != 5:
            errors.append(f"{where}: nutrition needs 5 values")
        if not isinstance(row.get("fdc_id"), int):
            errors.append(f"{where}: needs an fdc_id (USDA FoodData Central SR Legacy id)")
        if "class" in row:
            if row["class"] in classes:
                errors.append(f"{where}: class {row['class']} already used by {classes[row['class']]}")
            classes[row["class"]] = slug
        names = {a.lower() for a in row.get("aliases", [])}
        names |= {row["name"].lower(), slug.replace("_", " ")}
        row["all_aliases"] = sorted(names)
        for a in names:
            if aliases.get(a, slug) != slug:
                errors.append(f"{where}: alias '{a}' already maps to {aliases[a]}")
            aliases[a] = slug
        by_slug[slug] = row
    if sorted(classes) != list(range(len(classes))):
        errors.append(f"detector classes must be 0..n-1 without gaps, got {sorted(classes)}")
    return by_slug


def to_grams(qty: float, unit: str, ing: dict, where: str, errors: list[str]) -> float:
    if unit == "g":
        return qty
    if unit == "kg":
        return qty * 1000
    if unit == "pinch":
        return qty * PINCH_G
    if unit in COUNT_UNITS:
        if not ing.get("grams_per_unit") or ing.get("unit") != unit:
            errors.append(f"{where}: '{unit}' needs {ing['slug']} to have unit {unit} and grams_per_unit")
            return 0
        return qty * ing["grams_per_unit"]
    if unit in ML_PER:
        if not ing.get("density"):
            errors.append(f"{where}: '{unit}' needs a density for {ing['slug']}")
            return 0
        return qty * ML_PER[unit] * ing["density"]
    errors.append(f"{where}: unknown unit {unit}")
    return 0


def parse_line(line: str, where: str, errors: list[str]) -> dict | None:
    parts = [p.strip() for p in line.split("|")]
    head = parts[0].split()
    if len(head) != 3:
        errors.append(f"{where}: cannot parse '{line}'")
        return None
    qty_s, unit, slug = head
    try:
        qty = float(qty_s)
    except ValueError:
        errors.append(f"{where}: bad quantity in '{line}'")
        return None
    role, note = "core", None
    for extra in parts[1:]:
        if extra in ROLE_WEIGHTS:
            role = extra
        elif note is None:
            note = extra
        else:
            errors.append(f"{where}: too many fields in '{line}'")
    return {"qty": qty, "unit": unit, "slug": slug, "role": role, "note": note}


def load_recipes(ings: dict[str, dict], errors: list[str]) -> list[dict]:
    recipes = []
    seen = set()
    for path in sorted((DATA / "recipes").glob("*.yaml")):
        for r in yaml.safe_load(path.read_text()):
            where = f"{path.name}:{r.get('slug')}"
            if r["slug"] in seen:
                errors.append(f"{where}: duplicate recipe slug")
            seen.add(r["slug"])
            if r["meal"] not in MEALS:
                errors.append(f"{where}: unknown meal {r['meal']}")
            if r.get("difficulty", "easy") not in DIFFICULTIES:
                errors.append(f"{where}: unknown difficulty")
            if not r.get("steps"):
                errors.append(f"{where}: no steps")
            items, used = [], set()
            for line in r["ingredients"]:
                item = parse_line(line, where, errors)
                if item is None:
                    continue
                ing = ings.get(item["slug"])
                if ing is None:
                    errors.append(f"{where}: unknown ingredient {item['slug']}")
                    continue
                if item["slug"] in used:
                    errors.append(f"{where}: {item['slug']} listed twice")
                used.add(item["slug"])
                if item["unit"] not in UNITS:
                    errors.append(f"{where}: unknown unit {item['unit']}")
                item["grams"] = to_grams(item["qty"], item["unit"], ing, where, errors)
                item["weight"] = ROLE_WEIGHTS[item["role"]]
                items.append(item)
            r["items"] = items
            detectable = [i for i in items if "class" in ings[i["slug"]]]
            if len(detectable) < MIN_DETECTABLE_PER_RECIPE:
                errors.append(f"{where}: uses {len(detectable)} detectable ingredients, need {MIN_DETECTABLE_PER_RECIPE}")
            if not any(i["role"] == "core" and not ings[i["slug"]].get("staple") for i in items):
                errors.append(f"{where}: needs at least one non-staple core ingredient")
            r["nutrition"] = per_serving(r, ings)
            r["dietary_tags"] = dietary_tags(r, ings)
            recipes.append(r)
    return recipes


def per_serving(r: dict, ings: dict[str, dict]) -> dict[str, float]:
    totals = dict.fromkeys(NUTRIENTS, 0.0)
    for item in r["items"]:
        for k, v in zip(NUTRIENTS, ings[item["slug"]]["nutrition"]):
            totals[k] += item["grams"] / 100 * v
    return {k: v / r["servings"] for k, v in totals.items()}


def dietary_tags(r: dict, ings: dict[str, dict]) -> list[str]:
    used = [ings[i["slug"]] for i in r["items"]]
    allergens = {a for ing in used for a in ing.get("allergens", [])}
    diets = {ing.get("diet") for ing in used}
    tags = []
    if not diets & {"meat", "fish"}:
        tags.append("vegetarian")
        if not diets & {"animal"} and not allergens & {"milk", "egg"}:
            tags.append("vegan")
    if "gluten" not in allergens:
        tags.append("gluten_free")
    if "milk" not in allergens:
        tags.append("dairy_free")
    if r["nutrition"]["protein_g"] >= HIGH_PROTEIN_G:
        tags.append("high_protein")
    if r["nutrition"]["carbs_g"] <= LOW_CARB_G:
        tags.append("low_carb")
    return tags


def coverage_errors(recipes: list[dict], ings: dict[str, dict]) -> tuple[Counter, list[str]]:
    counts = Counter()
    for r in recipes:
        for i in r["items"]:
            if "class" in ings[i["slug"]]:
                counts[i["slug"]] += 1
    errors = [
        f"class {slug} appears in {counts[slug]} recipes, need {MIN_RECIPES_PER_CLASS}"
        for slug, ing in ings.items()
        if "class" in ing and counts[slug] < MIN_RECIPES_PER_CLASS
    ]
    return counts, errors


def values_block(rows: list[str]) -> str:
    return ",\n".join(f"  ({r})" for r in rows)


def ingredients_sql(ings: dict[str, dict]) -> str:
    rows, alias_rows, nut_rows = [], [], []
    for s, i in ings.items():
        rows.append(", ".join([
            q(s), q(i["name"]), q(i["category"]) + "::ingredient_category", q(bool(i.get("staple"))),
            q("class" in i), q(i.get("unit", "g")) + "::measure_unit", q(i.get("grams_per_unit")),
            q(i.get("shelf_life")), q(i.get("allergens", [])) + "::allergen[]",
        ]))
        alias_rows += [f"{q(a)}, {q(s)}" for a in i["all_aliases"]]
        nut_rows.append(", ".join([q(s)] + [q(float(v)) for v in i["nutrition"]] + [q(i["fdc_id"])]))
    return f"""-- Generated by scripts/build_seed.py from seed_data/ingredients.yaml. Do not edit by hand.
-- Safe to re-run: existing rows are updated to match the YAML.

insert into ingredients (slug, name, category, is_pantry_staple, is_detectable, default_unit,
                         grams_per_unit, shelf_life_days, allergens)
values
{values_block(rows)}
on conflict (slug) do update
  set name = excluded.name, category = excluded.category, is_pantry_staple = excluded.is_pantry_staple,
      is_detectable = excluded.is_detectable, default_unit = excluded.default_unit,
      grams_per_unit = excluded.grams_per_unit, shelf_life_days = excluded.shelf_life_days,
      allergens = excluded.allergens;

insert into ingredient_aliases (alias, ingredient_id)
select v.alias, i.id
from (values
{values_block(alias_rows)}
) as v(alias, slug)
join ingredients i on i.slug = v.slug
on conflict (alias) do nothing;

insert into ingredient_nutrition (ingredient_id, kcal, protein_g, carbs_g, fat_g, fiber_g, fdc_id, source)
select i.id, v.kcal, v.protein_g, v.carbs_g, v.fat_g, v.fiber_g, v.fdc_id, {q(NUTRITION_SOURCE)}
from (values
{values_block(nut_rows)}
) as v(slug, kcal, protein_g, carbs_g, fat_g, fiber_g, fdc_id)
join ingredients i on i.slug = v.slug
on conflict (ingredient_id) do update
  set kcal = excluded.kcal, protein_g = excluded.protein_g, carbs_g = excluded.carbs_g,
      fat_g = excluded.fat_g, fiber_g = excluded.fiber_g, fdc_id = excluded.fdc_id, source = excluded.source;

refresh materialized view recipe_nutrition;
"""


def recipes_sql(recipes: list[dict]) -> str:
    rec_rows, ing_rows, step_rows = [], [], []
    for r in recipes:
        rec_rows.append(", ".join([
            q(r["slug"]), q(r["title"]), q(r.get("description")), q(r.get("cuisine")),
            q(r["meal"]) + "::meal_type", q(r["servings"]), q(r["prep"]), q(r["cook"]),
            q(r.get("difficulty", "easy")) + "::difficulty", q(r["dietary_tags"]) + "::dietary_tag[]",
        ]))
        for i in r["items"]:
            ing_rows.append(", ".join([
                q(r["slug"]), q(i["slug"]), q(float(i["qty"])), q(i["unit"]) + "::measure_unit",
                q(float(i["grams"])), q(i["role"]) + "::ingredient_role", q(i["weight"]), q(i["note"]),
            ]))
        for n, step in enumerate(r["steps"], 1):
            step_rows.append(f"{q(r['slug'])}, {n}, {q(step)}")
    return f"""-- Generated by scripts/build_seed.py from seed_data/recipes/*.yaml. Do not edit by hand.
-- Safe to re-run: existing rows are updated to match the YAML. Run 01_ingredients.sql first.

insert into recipes (slug, title, description, cuisine, meal_type, servings, prep_minutes,
                     cook_minutes, difficulty, dietary_tags)
values
{values_block(rec_rows)}
on conflict (slug) do update
  set title = excluded.title, description = excluded.description, cuisine = excluded.cuisine,
      meal_type = excluded.meal_type, servings = excluded.servings, prep_minutes = excluded.prep_minutes,
      cook_minutes = excluded.cook_minutes, difficulty = excluded.difficulty,
      dietary_tags = excluded.dietary_tags;

insert into recipe_ingredients (recipe_id, ingredient_id, quantity, unit, grams, role, weight, note)
select r.id, i.id, v.quantity, v.unit, round(v.grams, 2), v.role, v.weight, v.note
from (values
{values_block(ing_rows)}
) as v(recipe_slug, ingredient_slug, quantity, unit, grams, role, weight, note)
join recipes r on r.slug = v.recipe_slug
join ingredients i on i.slug = v.ingredient_slug
on conflict (recipe_id, ingredient_id) do update
  set quantity = excluded.quantity, unit = excluded.unit, grams = excluded.grams,
      role = excluded.role, weight = excluded.weight, note = excluded.note;

insert into recipe_steps (recipe_id, step_number, instruction)
select r.id, v.step_number, v.instruction
from (values
{values_block(step_rows)}
) as v(recipe_slug, step_number, instruction)
join recipes r on r.slug = v.recipe_slug
on conflict (recipe_id, step_number) do update set instruction = excluded.instruction;

refresh materialized view recipe_nutrition;
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    args = parser.parse_args()

    errors: list[str] = []
    ings = load_ingredients(errors)
    recipes = load_recipes(ings, errors)
    counts, cov_errors = coverage_errors(recipes, ings)
    errors += cov_errors

    meals = Counter(r["meal"] for r in recipes)
    tags = Counter(t for r in recipes for t in r["dietary_tags"])
    print(f"{len(ings)} ingredients ({sum('class' in i for i in ings.values())} detectable, "
          f"{sum(bool(i.get('staple')) for i in ings.values())} staples); "
          f"{len(recipes)} recipes {dict(meals)}; "
          f"{sum(len(r['items']) for r in recipes)} recipe ingredients")
    print("dietary tags:", dict(tags))
    print("recipes per class:", ", ".join(f"{s} {counts[s]}" for s in sorted(counts, key=counts.get)))

    if errors:
        print(f"\n{len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1
    if not args.check:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "01_ingredients.sql").write_text(ingredients_sql(ings))
        (OUT / "02_recipes.sql").write_text(recipes_sql(recipes))
        print(f"wrote {OUT.relative_to(ROOT)}/01_ingredients.sql and 02_recipes.sql")
    return 0


if __name__ == "__main__":
    sys.exit(main())
