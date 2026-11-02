from __future__ import annotations

import uuid

import psycopg
import pytest

from conftest import auth_header, ingredient_id, jpeg

pytestmark = pytest.mark.anyio


# ---------- health and auth ----------

async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["detector"] == "fake"


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer not-a-jwt"},
    auth_header(uuid.uuid4(), exp_in=-10), auth_header(uuid.uuid4(), aud="anon"),
    auth_header(uuid.uuid4(), secret="a-different-secret-that-is-also-long-enough"),
])
async def test_rejects_bad_tokens(client, headers):
    r = await client.get("/v1/me", headers=headers)
    assert r.status_code == 401


# ---------- account ----------

async def test_profile_and_preferences(client, user):
    _, h = user
    me = (await client.get("/v1/me", headers=h)).json()
    assert me["display_name"] == "Tester"  # set by the sign-up trigger
    r = await client.patch("/v1/me", headers=h, json={"display_name": "Dhiransh"})
    assert r.json()["display_name"] == "Dhiransh"

    prefs = {"dietary_tags": ["vegetarian"], "excluded_allergens": ["peanut"], "nutrition_goal": "high_protein",
             "daily_kcal_target": 2200, "assume_staples": False}
    assert (await client.put("/v1/me/preferences", headers=h, json=prefs)).json() == prefs
    assert (await client.get("/v1/me/preferences", headers=h)).json() == prefs
    bad = await client.put("/v1/me/preferences", headers=h, json={**prefs, "excluded_allergens": ["cilantro"]})
    assert bad.status_code == 422


async def test_user_created_before_trigger_gets_rows(client, database_url):
    uid = uuid.uuid4()
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("alter table auth.users disable trigger on_auth_user_created")
        conn.execute("insert into auth.users (id) values (%s)", (uid,))
        conn.execute("alter table auth.users enable trigger on_auth_user_created")
    r = await client.get("/v1/me/preferences", headers=auth_header(uid))
    assert r.status_code == 200 and r.json()["assume_staples"] is True


# ---------- ingredients ----------

@pytest.mark.parametrize("q, slug", [("red onion", "onion"), ("lettuce", "leafy_greens"), ("Egg", "egg"),
                                     ("capsicum", "bell_pepper")])
async def test_ingredient_search(client, user, q, slug):
    r = await client.get("/v1/ingredients", headers=user[1], params={"q": q})
    assert r.json()[0]["slug"] == slug


async def test_ingredient_filter_detectable(client, user):
    r = await client.get("/v1/ingredients", headers=user[1], params={"detectable": True, "limit": 100})
    assert len(r.json()) == 25


# ---------- scans ----------

async def _scan(client, h, image=None):
    return await client.post("/v1/scans", headers=h, files={"image": ("fridge.jpg", image or jpeg(), "image/jpeg")})


async def test_scan_review_and_confirm(client, user, other_user, storage, database_url):
    uid, h = user
    r = await _scan(client, h, jpeg((400, 300), orientation=6))  # portrait phone photo stored sideways
    assert r.status_code == 201, r.text
    scan = r.json()
    assert scan["status"] == "awaiting_review"
    assert (scan["image_width"], scan["image_height"]) == (300, 400)  # EXIF rotation applied
    assert scan["image_url"].startswith("memory://") and f"{uid}/{scan['id']}.jpg" in storage.objects
    by_slug = {d["ingredient"]["slug"]: d for d in scan["detections"]}
    assert set(by_slug) == {"egg", "tomato", "cheese", "onion"}
    assert by_slug["onion"]["needs_check"] and not by_slug["egg"]["needs_check"]

    assert (await client.get(f"/v1/scans/{scan['id']}", headers=other_user[1])).status_code == 404

    butter, bread = ingredient_id(database_url, "butter"), ingredient_id(database_url, "bread")
    decisions = [{"detection_id": by_slug["egg"]["id"], "action": "accept"},
                 {"detection_id": by_slug["tomato"]["id"], "action": "accept"},
                 {"detection_id": by_slug["cheese"]["id"], "action": "relabel", "ingredient_id": butter},
                 {"detection_id": by_slug["onion"]["id"], "action": "reject"}]
    r = await client.post(f"/v1/scans/{scan['id']}/confirm", headers=h,
                          json={"decisions": decisions, "additions": [{"ingredient_id": bread, "quantity": 6, "unit": "slice"}]})
    assert r.status_code == 200, r.text
    fridge = {i["ingredient"]["slug"]: i for i in r.json()}
    assert set(fridge) == {"egg", "tomato", "butter", "bread"}
    assert fridge["egg"]["days_left"] == 35 and fridge["egg"]["source"] == "scan"  # shelf life from the catalog
    assert fridge["bread"]["quantity"] == 6 and fridge["bread"]["unit"] == "slice"

    confirmed = (await client.get(f"/v1/scans/{scan['id']}", headers=h)).json()
    assert confirmed["status"] == "confirmed"
    reviews = {d["ingredient"]["slug"]: (d["review"], (d["corrected_ingredient"] or {}).get("slug"))
               for d in confirmed["detections"]}
    assert reviews["cheese"] == ("relabeled", "butter") and reviews["onion"] == ("rejected", None)

    again = await client.post(f"/v1/scans/{scan['id']}/confirm", headers=h, json={"decisions": decisions})
    assert again.status_code == 409
    listed = (await client.get("/v1/scans", headers=h)).json()
    assert listed[0]["id"] == scan["id"] and listed[0]["detection_count"] == 4


async def test_confirm_requires_a_decision_for_every_detection(client, user, database_url):
    h = user[1]
    scan = (await _scan(client, h)).json()
    first = scan["detections"][0]["id"]
    r = await client.post(f"/v1/scans/{scan['id']}/confirm", headers=h,
                          json={"decisions": [{"detection_id": first, "action": "accept"}]})
    assert r.status_code == 422 and "missing" in r.json()["detail"]

    all_accept = [{"detection_id": d["id"], "action": "accept"} for d in scan["detections"]]
    r = await client.post(f"/v1/scans/{scan['id']}/confirm", headers=h,
                          json={"decisions": all_accept, "additions": [{"ingredient_id": 999999}]})
    assert r.status_code == 422
    assert (await client.get("/v1/inventory", headers=h)).json() == []  # nothing half-applied
    assert (await client.get(f"/v1/scans/{scan['id']}", headers=h)).json()["status"] == "awaiting_review"


async def test_scan_rejects_bad_uploads(client, user):
    h = user[1]
    assert (await _scan(client, h, b"definitely not an image")).status_code == 415
    assert (await _scan(client, h, b"\xff" * (3 * 1024 * 1024))).status_code == 413


async def test_storage_failure_returns_502(client, user, storage):
    async def down(*args):
        raise ConnectionError("storage unreachable")
    storage.upload = down
    assert (await _scan(client, user[1])).status_code == 502
    assert (await client.get("/v1/scans", headers=user[1])).json() == []


async def test_detector_failure_marks_scan_failed(client, user):
    def boom(image):
        raise RuntimeError("model crashed")
    client.app.state.detector.detect = boom
    r = await _scan(client, user[1])
    assert r.status_code == 201
    assert r.json()["status"] == "failed" and "model crashed" in r.json()["error_message"]


# ---------- inventory ----------

async def test_inventory_crud_and_isolation(client, user, other_user, database_url):
    h = user[1]
    milk = ingredient_id(database_url, "milk")
    r = await client.post("/v1/inventory", headers=h, json={"ingredient_id": milk, "quantity": 1000, "unit": "ml",
                                                            "expires_at": "2026-11-05"})
    assert r.status_code == 201
    item = r.json()
    assert item["ingredient"]["slug"] == "milk" and item["source"] == "manual" and item["expires_at"] == "2026-11-05"

    r = await client.post("/v1/inventory", headers=h, json={"ingredient_id": milk, "quantity": 500})
    assert r.json()["id"] == item["id"] and r.json()["quantity"] == 500 and r.json()["expires_at"] == "2026-11-05"

    r = await client.patch(f"/v1/inventory/{item['id']}", headers=h, json={"expires_at": None})
    assert r.json()["expires_at"] is None and r.json()["quantity"] == 500

    assert (await client.patch(f"/v1/inventory/{item['id']}", headers=other_user[1], json={"quantity": 1})).status_code == 404
    assert (await client.delete(f"/v1/inventory/{item['id']}", headers=other_user[1])).status_code == 404
    assert (await client.delete(f"/v1/inventory/{item['id']}", headers=h)).status_code == 204
    assert (await client.get("/v1/inventory", headers=h)).json() == []
    assert (await client.post("/v1/inventory", headers=h, json={"ingredient_id": 999999})).status_code == 422


# ---------- recommendations and recipes ----------

async def _stock(client, h, database_url, slugs):
    for s in slugs:
        r = await client.post("/v1/inventory", headers=h, json={"ingredient_id": ingredient_id(database_url, s)})
        assert r.status_code == 201


async def test_recommendations(client, user, database_url):
    uid, h = user
    empty = (await client.get("/v1/recommendations", headers=h)).json()
    assert empty["inventory_count"] == 0 and empty["items"] == []

    await _stock(client, h, database_url, ["egg", "tomato", "onion", "bell_pepper", "cheese", "bread"])
    rec = (await client.get("/v1/recommendations", headers=h, params={"limit": 3})).json()
    assert rec["inventory_count"] == 6
    assert [(i["recipe"]["title"], i["weighted_score"]) for i in rec["items"]] == [
        ("Tomato Cheese Toast", 0.9375), ("Veggie Scramble", 0.875), ("Shakshuka", 0.7692)]
    top = rec["items"][0]
    assert top["rank"] == 1 and top["missing_ingredients"] == ["Basil"] and top["recipe"]["nutrition_per_serving"]["kcal"] > 0

    with psycopg.connect(database_url) as conn:
        n = conn.execute("select count(*) from recommendation_items ri join recommendation_runs rr on rr.id = ri.run_id "
                         "where rr.id = %s and rr.user_id = %s", (rec["run_id"], uid)).fetchone()[0]
    assert n == 3

    prefs = (await client.get("/v1/me/preferences", headers=h)).json()
    await client.put("/v1/me/preferences", headers=h, json={**prefs, "excluded_allergens": ["milk"]})
    no_milk = (await client.get("/v1/recommendations", headers=h, params={"limit": 50})).json()["items"]
    ids = [i["recipe"]["id"] for i in no_milk]
    with psycopg.connect(database_url) as conn:
        milky = conn.execute("select count(*) from recipe_ingredients ri join ingredients i on i.id = ri.ingredient_id "
                             "where ri.recipe_id = any(%s) and 'milk' = any(i.allergens)", (ids,)).fetchone()[0]
    assert ids and milky == 0


async def test_recipe_browse_detail_save_and_cook(client, user, database_url):
    h = user[1]
    snacks = (await client.get("/v1/recipes", headers=h, params={"meal_type": "snack", "limit": 100})).json()
    assert len(snacks) == 15 and all(r["meal_type"] == "snack" for r in snacks)
    found = (await client.get("/v1/recipes", headers=h, params={"q": "eggplant"})).json()
    assert any(r["slug"] == "baba-ganoush" for r in found)

    await _stock(client, h, database_url, ["egg", "tomato", "onion"])
    omelette = next(r for r in (await client.get("/v1/recipes", headers=h, params={"q": "Tomato Omelette"})).json())
    detail = (await client.get(f"/v1/recipes/{omelette['id']}", headers=h)).json()
    fridge = {i["ingredient"]["slug"]: i["in_fridge"] for i in detail["ingredients"]}
    assert fridge["egg"] and fridge["tomato"] and not fridge["butter"]
    assert detail["ingredients"][0]["role"] == "core" and len(detail["steps"]) == 5 and detail["saved"] is False

    assert (await client.put(f"/v1/recipes/{omelette['id']}/save", headers=h)).status_code == 204
    assert [r["id"] for r in (await client.get("/v1/recipes/saved", headers=h)).json()] == [omelette["id"]]
    assert (await client.get(f"/v1/recipes/{omelette['id']}", headers=h)).json()["saved"] is True
    await client.delete(f"/v1/recipes/{omelette['id']}/save", headers=h)
    assert (await client.get("/v1/recipes/saved", headers=h)).json() == []

    r = await client.post(f"/v1/recipes/{omelette['id']}/cooked", headers=h, json={"rating": 5, "remove_used": True})
    assert r.status_code == 201
    # Core ingredients are egg, tomato and butter; butter was never in the fridge.
    assert {i["slug"] for i in r.json()["removed_from_fridge"]} == {"egg", "tomato"}
    left = {i["ingredient"]["slug"] for i in (await client.get("/v1/inventory", headers=h)).json()}
    assert left == {"onion"}  # onion is only a supporting ingredient
    history = (await client.get("/v1/recipes/history", headers=h)).json()
    assert history[0]["recipe"]["id"] == omelette["id"] and history[0]["rating"] == 5

    assert (await client.get("/v1/recipes/999999", headers=h)).status_code == 404
    assert (await client.post("/v1/recipes/999999/cooked", headers=h, json={})).status_code == 404
