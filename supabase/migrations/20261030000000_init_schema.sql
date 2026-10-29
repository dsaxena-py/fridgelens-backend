-- MealLens: enums, tables, indexes, triggers

-- ---------- Enums ----------
create type ingredient_category as enum
  ('produce', 'dairy', 'protein', 'bakery', 'grain', 'pantry', 'spice', 'condiment', 'other');
create type allergen as enum
  ('milk', 'egg', 'gluten', 'soy', 'peanut', 'tree_nut', 'fish', 'shellfish', 'sesame');
create type dietary_tag as enum
  ('vegetarian', 'vegan', 'gluten_free', 'dairy_free', 'high_protein', 'low_carb');
create type measure_unit as enum
  ('g', 'kg', 'ml', 'l', 'piece', 'tsp', 'tbsp', 'cup', 'clove', 'slice', 'pinch');
create type ingredient_role as enum ('core', 'supporting', 'garnish');
create type meal_type as enum ('breakfast', 'lunch', 'dinner', 'snack', 'dessert');
create type difficulty as enum ('easy', 'medium', 'hard');
create type nutrition_goal as enum ('none', 'high_protein', 'low_calorie', 'low_carb', 'high_fiber');
create type scan_status as enum ('processing', 'awaiting_review', 'confirmed', 'failed');
create type detection_review as enum ('pending', 'accepted', 'rejected', 'relabeled');
create type inventory_source as enum ('scan', 'manual');

-- ---------- Shared trigger ----------
create function set_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- ---------- Users ----------
create table profiles (
  id            uuid primary key references auth.users (id) on delete cascade,
  display_name  text check (char_length(display_name) <= 60),
  avatar_url    text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table user_preferences (
  user_id             uuid primary key references profiles (id) on delete cascade,
  dietary_tags        dietary_tag[] not null default '{}',
  excluded_allergens  allergen[]    not null default '{}',
  nutrition_goal      nutrition_goal not null default 'none',
  daily_kcal_target   integer check (daily_kcal_target between 800 and 6000),
  assume_staples      boolean not null default true,
  -- Score(r) = alpha*M + beta*N + gamma*E; MVP uses alpha = 1
  score_alpha         numeric(3,2) not null default 1.00,
  score_beta          numeric(3,2) not null default 0.00,
  score_gamma         numeric(3,2) not null default 0.00,
  updated_at          timestamptz not null default now(),
  constraint score_weights_sum_to_one check (score_alpha + score_beta + score_gamma = 1)
);

-- ---------- Ingredient catalog ----------
create table ingredients (
  id                   integer generated always as identity primary key,
  slug                 text not null unique check (slug ~ '^[a-z0-9_]+$'),
  name                 text not null,
  category             ingredient_category not null,
  is_pantry_staple     boolean not null default false,
  is_detectable        boolean not null default false,
  default_unit         measure_unit not null default 'g',
  grams_per_unit       numeric(8,2) check (grams_per_unit > 0),
  shelf_life_days      smallint check (shelf_life_days > 0),
  allergens            allergen[] not null default '{}',
  created_at           timestamptz not null default now()
);

create table ingredient_aliases (
  alias          text primary key check (alias = lower(alias)),
  ingredient_id  integer not null references ingredients (id) on delete cascade
);
create index on ingredient_aliases (ingredient_id);

create table ingredient_nutrition (
  ingredient_id  integer primary key references ingredients (id) on delete cascade,
  kcal           numeric(7,2) not null check (kcal >= 0),
  protein_g      numeric(6,2) not null check (protein_g >= 0),
  carbs_g        numeric(6,2) not null check (carbs_g >= 0),
  fat_g          numeric(6,2) not null check (fat_g >= 0),
  fiber_g        numeric(6,2) not null check (fiber_g >= 0),
  fdc_id         integer,
  source         text not null default 'USDA FoodData Central SR Legacy'
);

-- ---------- Vision ----------
create table model_versions (
  id              integer generated always as identity primary key,
  name            text not null unique,
  architecture    text not null,
  weights_uri     text not null,
  input_size      smallint not null default 640,
  conf_threshold  numeric(3,2) not null default 0.35 check (conf_threshold between 0 and 1),
  iou_threshold   numeric(3,2) not null default 0.50 check (iou_threshold between 0 and 1),
  metrics         jsonb not null default '{}',
  is_active       boolean not null default false,
  created_at      timestamptz not null default now()
);
create unique index one_active_model on model_versions (is_active) where is_active;

create table detector_classes (
  model_version_id  integer not null references model_versions (id) on delete cascade,
  class_index       smallint not null check (class_index >= 0),
  label             text not null,
  ingredient_id     integer not null references ingredients (id),
  primary key (model_version_id, class_index),
  unique (model_version_id, label)
);

create table scans (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references profiles (id) on delete cascade,
  model_version_id  integer references model_versions (id),
  image_path        text not null,
  image_width       integer check (image_width > 0),
  image_height      integer check (image_height > 0),
  status            scan_status not null default 'processing',
  inference_ms      integer check (inference_ms >= 0),
  error_message     text,
  created_at        timestamptz not null default now(),
  confirmed_at      timestamptz
);
create index on scans (user_id, created_at desc);

create table detections (
  id                       bigint generated always as identity primary key,
  scan_id                  uuid not null references scans (id) on delete cascade,
  class_index              smallint not null,
  ingredient_id            integer not null references ingredients (id),
  confidence               real not null check (confidence between 0 and 1),
  -- bounding box, normalised to [0, 1] of image width/height
  x_min real not null check (x_min between 0 and 1),
  y_min real not null check (y_min between 0 and 1),
  x_max real not null check (x_max between 0 and 1),
  y_max real not null check (y_max between 0 and 1),
  review                   detection_review not null default 'pending',
  corrected_ingredient_id  integer references ingredients (id),
  constraint bbox_ordered check (x_min < x_max and y_min < y_max),
  constraint relabel_has_target check ((review = 'relabeled') = (corrected_ingredient_id is not null))
);
create index on detections (scan_id);

-- ---------- Inventory ----------
create table inventory_items (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references profiles (id) on delete cascade,
  ingredient_id   integer not null references ingredients (id),
  quantity        numeric(8,2) check (quantity > 0),
  unit            measure_unit,
  source          inventory_source not null,
  source_scan_id  uuid references scans (id) on delete set null,
  expires_at      date,
  added_at        timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (user_id, ingredient_id)
);

-- ---------- Recipes ----------
create table recipes (
  id            integer generated always as identity primary key,
  slug          text not null unique check (slug ~ '^[a-z0-9-]+$'),
  title         text not null,
  description   text,
  cuisine       text,
  meal_type     meal_type not null,
  servings      smallint not null check (servings > 0),
  prep_minutes  smallint not null check (prep_minutes >= 0),
  cook_minutes  smallint not null check (cook_minutes >= 0),
  difficulty    difficulty not null default 'easy',
  dietary_tags  dietary_tag[] not null default '{}',
  image_url     text,
  source_url    text,
  is_published  boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index on recipes using gin (dietary_tags);

create table recipe_ingredients (
  recipe_id      integer not null references recipes (id) on delete cascade,
  ingredient_id  integer not null references ingredients (id),
  quantity       numeric(8,2) not null check (quantity > 0),
  unit           measure_unit not null,
  grams          numeric(8,2) not null check (grams >= 0),
  role           ingredient_role not null default 'core',
  weight         numeric(3,2) not null check (weight > 0 and weight <= 1),
  note           text,
  primary key (recipe_id, ingredient_id)
);
create index on recipe_ingredients (ingredient_id);

create table recipe_steps (
  recipe_id    integer not null references recipes (id) on delete cascade,
  step_number  smallint not null check (step_number > 0),
  instruction  text not null,
  primary key (recipe_id, step_number)
);

-- ---------- User activity ----------
create table saved_recipes (
  user_id    uuid not null references profiles (id) on delete cascade,
  recipe_id  integer not null references recipes (id) on delete cascade,
  saved_at   timestamptz not null default now(),
  primary key (user_id, recipe_id)
);

create table cooking_history (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references profiles (id) on delete cascade,
  recipe_id  integer not null references recipes (id) on delete cascade,
  cooked_at  timestamptz not null default now(),
  rating     smallint check (rating between 1 and 5)
);
create index on cooking_history (user_id, cooked_at desc);

create table recommendation_runs (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references profiles (id) on delete cascade,
  scan_id     uuid references scans (id) on delete set null,
  params      jsonb not null default '{}',
  latency_ms  integer,
  created_at  timestamptz not null default now()
);
create index on recommendation_runs (user_id, created_at desc);

create table recommendation_items (
  run_id          uuid not null references recommendation_runs (id) on delete cascade,
  rank            smallint not null check (rank > 0),
  recipe_id       integer not null references recipes (id) on delete cascade,
  match_score     numeric(5,4) not null,
  weighted_score  numeric(5,4) not null,
  final_score     numeric(5,4) not null,
  missing_count   smallint not null,
  primary key (run_id, rank)
);

-- ---------- updated_at triggers ----------
create trigger profiles_updated        before update on profiles        for each row execute function set_updated_at();
create trigger preferences_updated     before update on user_preferences for each row execute function set_updated_at();
create trigger inventory_updated       before update on inventory_items for each row execute function set_updated_at();
create trigger recipes_updated         before update on recipes         for each row execute function set_updated_at();

-- ---------- New user bootstrap ----------
create function handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into profiles (id, display_name)
  values (new.id, new.raw_user_meta_data ->> 'display_name');
  insert into user_preferences (user_id) values (new.id);
  return new;
end $$;

create trigger on_auth_user_created
  after insert on auth.users for each row execute function handle_new_user();

