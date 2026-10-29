-- MealLens: row level security and storage

-- ---------- Row Level Security ----------
alter table profiles             enable row level security;
alter table user_preferences     enable row level security;
alter table scans                enable row level security;
alter table detections           enable row level security;
alter table inventory_items      enable row level security;
alter table saved_recipes        enable row level security;
alter table cooking_history      enable row level security;
alter table recommendation_runs  enable row level security;
alter table recommendation_items enable row level security;
alter table ingredients          enable row level security;
alter table ingredient_aliases   enable row level security;
alter table ingredient_nutrition enable row level security;
alter table model_versions       enable row level security;
alter table detector_classes     enable row level security;
alter table recipes              enable row level security;
alter table recipe_ingredients   enable row level security;
alter table recipe_steps         enable row level security;

-- Owner-only tables keyed by user_id
create policy own_rows on user_preferences    for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy own_rows on scans               for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy own_rows on inventory_items     for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy own_rows on saved_recipes       for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy own_rows on cooking_history     for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy own_rows on recommendation_runs for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy own_profile on profiles         for all to authenticated using (id = auth.uid()) with check (id = auth.uid());

-- Child tables inherit ownership from their parent
create policy own_rows on detections for all to authenticated
  using (exists (select 1 from scans s where s.id = scan_id and s.user_id = auth.uid()))
  with check (exists (select 1 from scans s where s.id = scan_id and s.user_id = auth.uid()));
create policy own_rows on recommendation_items for select to authenticated
  using (exists (select 1 from recommendation_runs rr where rr.id = run_id and rr.user_id = auth.uid()));

-- Catalog tables: read-only for signed-in users; writes via service role / migrations only
create policy read_catalog on ingredients          for select to authenticated using (true);
create policy read_catalog on ingredient_aliases   for select to authenticated using (true);
create policy read_catalog on ingredient_nutrition for select to authenticated using (true);
create policy read_catalog on model_versions       for select to authenticated using (true);
create policy read_catalog on detector_classes     for select to authenticated using (true);
create policy read_catalog on recipes              for select to authenticated using (is_published);
create policy read_catalog on recipe_ingredients   for select to authenticated using (true);
create policy read_catalog on recipe_steps         for select to authenticated using (true);

-- ---------- Storage ----------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('scan-images', 'scan-images', false, 10485760, array['image/jpeg', 'image/png', 'image/heic']);

create policy scan_images_owner on storage.objects for all to authenticated
  using (bucket_id = 'scan-images' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'scan-images' and (storage.foldername(name))[1] = auth.uid()::text);
