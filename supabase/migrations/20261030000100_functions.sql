-- MealLens: nutrition view and recipe recommendation

-- ---------- Recipe nutrition ----------
create materialized view recipe_nutrition as
select r.id as recipe_id,
       round(sum(ri.grams / 100 * n.kcal)      / r.servings, 1) as kcal_per_serving,
       round(sum(ri.grams / 100 * n.protein_g) / r.servings, 1) as protein_g_per_serving,
       round(sum(ri.grams / 100 * n.carbs_g)   / r.servings, 1) as carbs_g_per_serving,
       round(sum(ri.grams / 100 * n.fat_g)     / r.servings, 1) as fat_g_per_serving,
       round(sum(ri.grams / 100 * n.fiber_g)   / r.servings, 1) as fiber_g_per_serving
from recipes r
join recipe_ingredients ri on ri.recipe_id = r.id
join ingredient_nutrition n on n.ingredient_id = ri.ingredient_id
group by r.id, r.servings;
create unique index on recipe_nutrition (recipe_id);

-- ---------- Recommendation ----------
create function recommend_recipes(p_user_id uuid, p_limit integer default 10)
returns table (
  recipe_id            integer,
  title                text,
  match_score          numeric,
  weighted_score       numeric,
  missing_count        integer,
  missing_ingredients  text[]
)
language sql stable as $$
  with prefs as (
    select * from user_preferences where user_id = p_user_id
  ),
  scored as (
    select ri.recipe_id,
           ri.weight,
           ri.role,
           i.name,
           exists (select 1 from inventory_items inv
                   where inv.user_id = p_user_id and inv.ingredient_id = ri.ingredient_id) as have
    from recipe_ingredients ri
    join ingredients i on i.id = ri.ingredient_id
    cross join prefs
    where not (prefs.assume_staples and i.is_pantry_staple)
  ),
  agg as (
    select s.recipe_id,
           count(*) filter (where s.have)::numeric / count(*)                        as match_score,
           sum(s.weight) filter (where s.have) / sum(s.weight)                        as weighted_score,
           count(*) filter (where not s.have and s.role <> 'garnish')::integer        as missing_count,
           array_agg(s.name order by s.weight desc) filter (where not s.have)         as missing_ingredients
    from scored s
    group by s.recipe_id
    having bool_or(s.have)
  )
  select r.id, r.title,
         round(a.match_score, 4), round(coalesce(a.weighted_score, 0), 4),
         a.missing_count, coalesce(a.missing_ingredients, '{}')
  from agg a
  join recipes r on r.id = a.recipe_id
  cross join prefs
  where r.is_published
    and r.dietary_tags @> prefs.dietary_tags
    and not exists (
      select 1 from recipe_ingredients ri2
      join ingredients i2 on i2.id = ri2.ingredient_id
      where ri2.recipe_id = r.id and i2.allergens && prefs.excluded_allergens)
  order by a.weighted_score desc, a.missing_count asc, r.prep_minutes + r.cook_minutes asc
  limit p_limit
$$;

