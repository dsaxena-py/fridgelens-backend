"""Request and response models. Field names follow the database columns."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

DietaryTag = Literal["vegetarian", "vegan", "gluten_free", "dairy_free", "high_protein", "low_carb"]
Allergen = Literal["milk", "egg", "gluten", "soy", "peanut", "tree_nut", "fish", "shellfish", "sesame"]
NutritionGoal = Literal["none", "high_protein", "low_calorie", "low_carb", "high_fiber"]
Unit = Literal["g", "kg", "ml", "l", "piece", "tsp", "tbsp", "cup", "clove", "slice", "pinch"]
MealType = Literal["breakfast", "lunch", "dinner", "snack", "dessert"]


# ---------- Users ----------

class Profile(BaseModel):
    id: UUID
    email: str | None
    display_name: str | None
    avatar_url: str | None
    created_at: datetime


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=60)
    avatar_url: str | None = None


class Preferences(BaseModel):
    dietary_tags: list[DietaryTag] = []
    excluded_allergens: list[Allergen] = []
    nutrition_goal: NutritionGoal = "none"
    daily_kcal_target: int | None = Field(default=None, ge=800, le=6000)
    assume_staples: bool = True


# ---------- Ingredients ----------

class IngredientRef(BaseModel):
    id: int
    slug: str
    name: str


class Ingredient(IngredientRef):
    category: str
    is_pantry_staple: bool
    is_detectable: bool
    default_unit: Unit
    allergens: list[Allergen]


# ---------- Scans ----------

class Box(BaseModel):
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class DetectionOut(BaseModel):
    id: int
    ingredient: IngredientRef
    confidence: float
    box: Box
    review: Literal["pending", "accepted", "rejected", "relabeled"]
    corrected_ingredient: IngredientRef | None
    needs_check: bool = Field(description="confidence below the review threshold: ask the user to look closely")


class ScanOut(BaseModel):
    id: UUID
    status: Literal["processing", "awaiting_review", "confirmed", "failed"]
    image_url: str | None
    image_width: int | None
    image_height: int | None
    model: str | None
    inference_ms: int | None
    error_message: str | None
    created_at: datetime
    confirmed_at: datetime | None
    detections: list[DetectionOut]


class ScanSummary(BaseModel):
    id: UUID
    status: str
    created_at: datetime
    confirmed_at: datetime | None
    detection_count: int


class DetectionDecision(BaseModel):
    detection_id: int
    action: Literal["accept", "reject", "relabel"]
    ingredient_id: int | None = Field(default=None, description="required for relabel")


class InventoryAddition(BaseModel):
    ingredient_id: int
    quantity: float | None = Field(default=None, gt=0)
    unit: Unit | None = None


class ConfirmScan(BaseModel):
    decisions: list[DetectionDecision] = Field(description="one decision for every detection of the scan")
    additions: list[InventoryAddition] = Field(default=[], description="items the camera missed")


# ---------- Inventory ----------

class InventoryItem(BaseModel):
    id: UUID
    ingredient: Ingredient
    quantity: float | None
    unit: Unit | None
    source: Literal["scan", "manual"]
    source_scan_id: UUID | None
    expires_at: date | None
    days_left: int | None
    added_at: datetime
    updated_at: datetime


class InventoryCreate(BaseModel):
    ingredient_id: int
    quantity: float | None = Field(default=None, gt=0)
    unit: Unit | None = None
    expires_at: date | None = None


class InventoryUpdate(BaseModel):
    quantity: float | None = Field(default=None, gt=0)
    unit: Unit | None = None
    expires_at: date | None = None


# ---------- Recipes ----------

class Nutrition(BaseModel):
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float


class RecipeSummary(BaseModel):
    id: int
    slug: str
    title: str
    description: str | None
    cuisine: str | None
    meal_type: MealType
    servings: int
    prep_minutes: int
    cook_minutes: int
    difficulty: Literal["easy", "medium", "hard"]
    dietary_tags: list[DietaryTag]
    image_url: str | None
    nutrition_per_serving: Nutrition | None


class RecipeIngredient(BaseModel):
    ingredient: IngredientRef
    quantity: float
    unit: Unit
    grams: float
    role: Literal["core", "supporting", "garnish"]
    note: str | None
    is_pantry_staple: bool
    in_fridge: bool


class RecipeDetail(RecipeSummary):
    ingredients: list[RecipeIngredient]
    steps: list[str]
    saved: bool


class Recommendation(BaseModel):
    rank: int
    recipe: RecipeSummary
    match_score: float = Field(description="share of the recipe's ingredients you have")
    weighted_score: float = Field(description="the same, weighting core 1.0, supporting 0.5, garnish 0.2")
    missing_count: int = Field(description="missing core and supporting ingredients")
    missing_ingredients: list[str]


class Recommendations(BaseModel):
    run_id: UUID
    inventory_count: int
    items: list[Recommendation]


class CookedRequest(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    remove_used: bool = Field(default=False, description="remove the recipe's core ingredients from the fridge")


class CookedOut(BaseModel):
    id: int
    cooked_at: datetime
    removed_from_fridge: list[IngredientRef]


class CookingEntry(BaseModel):
    id: int
    recipe: RecipeSummary
    cooked_at: datetime
    rating: int | None
