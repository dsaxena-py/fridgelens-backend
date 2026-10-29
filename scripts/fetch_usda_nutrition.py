#!/usr/bin/env python3
"""Refresh ingredient nutrition in seed_data/ingredients.yaml from USDA FoodData Central.

Every ingredient carries an `fdc_id` (an SR Legacy entry). This script fetches those
entries and rewrites each `nutrition: [kcal, protein, carbs, fat, fiber]` list in place,
per 100 g, printing every value that changed.

Usage:
    FDC_API_KEY=... python3 scripts/fetch_usda_nutrition.py           # update the YAML
    FDC_API_KEY=... python3 scripts/fetch_usda_nutrition.py --dry-run # only show changes

Get a free key at https://fdc.nal.usda.gov/api-key-signup. Run build_seed.py afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
INGREDIENTS = ROOT / "seed_data" / "ingredients.yaml"
API = "https://api.nal.usda.gov/fdc/v1/foods"
BATCH = 20

# SR Legacy nutrient numbers, in the order of the YAML `nutrition` list.
NUTRIENT_NUMBERS = ("208", "203", "205", "204", "291")  # kcal, protein, carbs, fat, fiber


def fetch(ids: list[int], key: str) -> list[dict]:
    body = json.dumps({"fdcIds": ids, "format": "full"}).encode()
    req = urllib.request.Request(f"{API}?api_key={key}", data=body, headers={"Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except Exception as e:  # network hiccup or rate limit
            if attempt == 3:
                raise
            print(f"  retrying after {e}", file=sys.stderr)
            time.sleep(2 ** (attempt + 1))
    return []


def nutrition_of(food: dict) -> list[float]:
    by_number = {n["nutrient"]["number"]: n.get("amount", 0) for n in food.get("foodNutrients", []) if "nutrient" in n}
    return [round(float(by_number.get(num, 0)), 1) for num in NUTRIENT_NUMBERS]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("FDC_API_KEY")
    if not key:
        print("Set FDC_API_KEY", file=sys.stderr)
        return 1

    rows = yaml.safe_load(INGREDIENTS.read_text())
    by_id = {r["fdc_id"]: r for r in rows}
    foods = []
    ids = list(by_id)
    for i in range(0, len(ids), BATCH):
        foods += fetch(ids[i:i + BATCH], key)

    text = INGREDIENTS.read_text()
    changed = 0
    for food in foods:
        row = by_id[food["fdcId"]]
        new = nutrition_of(food)
        old = [round(float(v), 1) for v in row["nutrition"]]
        if new != old:
            changed += 1
            print(f"{row['slug']:20} {old} -> {new}   ({food['description']})")
        # Rewrite this ingredient's nutrition list, which sits between its slug and the next entry.
        pattern = re.compile(r"(\{slug: %s, .*?nutrition: )\[[^\]]*\]" % re.escape(row["slug"]), re.S)
        text, n = pattern.subn(lambda m: m.group(1) + "[" + ", ".join(f"{v:g}" for v in new) + "]", text, count=1)
        if n != 1:
            print(f"could not find nutrition for {row['slug']}", file=sys.stderr)
            return 1
    missing = set(by_id) - {f["fdcId"] for f in foods}
    if missing:
        print(f"FDC returned nothing for {sorted(missing)}", file=sys.stderr)
        return 1
    print(f"{changed} of {len(rows)} ingredients changed")
    if not args.dry_run:
        INGREDIENTS.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
