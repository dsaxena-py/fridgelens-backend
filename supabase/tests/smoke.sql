-- Smoke test: tiny catalog, one user, one recommendation
insert into ingredients (slug, name, category, is_pantry_staple, is_detectable, default_unit, grams_per_unit, allergens) values
 ('egg','Egg','protein',false,true,'piece',50,'{egg}'),
 ('tomato','Tomato','produce',false,true,'piece',120,'{}'),
 ('onion','Onion','produce',false,true,'piece',110,'{}'),
 ('cheese','Cheese','dairy',false,true,'g',null,'{milk}'),
 ('salt','Salt','spice',true,false,'g',null,'{}'),
 ('parsley','Parsley','produce',false,false,'g',null,'{}');
insert into ingredient_aliases values ('red onion',3),('yellow onion',3);
insert into ingredient_nutrition (ingredient_id,kcal,protein_g,carbs_g,fat_g,fiber_g) values
 (1,143,12.6,0.7,9.5,0),(2,18,0.9,3.9,0.2,1.2),(3,40,1.1,9.3,0.1,1.7),(4,402,22.9,3.1,33.1,0),(5,0,0,0,0,0),(6,36,3,6.3,0.8,3.3);
insert into recipes (slug,title,meal_type,servings,prep_minutes,cook_minutes,dietary_tags) values
 ('tomato-omelette','Tomato Omelette','breakfast',1,5,8,'{vegetarian}'),
 ('cheese-omelette','Cheese Omelette','breakfast',1,5,8,'{vegetarian}');
insert into recipe_ingredients values
 (1,1,2,'piece',100,'core',1.0,null),(1,2,1,'piece',120,'core',1.0,null),(1,3,0.5,'piece',55,'supporting',0.5,null),(1,5,1,'pinch',0.5,'core',1.0,null),(1,6,5,'g',5,'garnish',0.2,null),
 (2,1,2,'piece',100,'core',1.0,null),(2,4,30,'g',30,'core',1.0,null);
refresh materialized view recipe_nutrition;

insert into auth.users (id, raw_user_meta_data) values ('11111111-1111-1111-1111-111111111111','{"display_name":"Dhiransh"}');
insert into inventory_items (user_id, ingredient_id, source) values
 ('11111111-1111-1111-1111-111111111111',1,'manual'),('11111111-1111-1111-1111-111111111111',2,'manual');
update user_preferences set updated_at = updated_at where user_id = '11111111-1111-1111-1111-111111111111';

select * from profiles;
select * from recipe_nutrition order by recipe_id;
select * from recommend_recipes('11111111-1111-1111-1111-111111111111');
update user_preferences set excluded_allergens = '{milk}';
select recipe_id, title from recommend_recipes('11111111-1111-1111-1111-111111111111');
-- constraint checks (should fail)
\set ON_ERROR_STOP off
insert into detections (scan_id,class_index,ingredient_id,confidence,x_min,y_min,x_max,y_max) values (gen_random_uuid(),0,1,0.9,0.5,0.1,0.4,0.2);
update user_preferences set score_alpha = 0.5;
