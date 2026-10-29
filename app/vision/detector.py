"""Ingredient detectors: the exported ONNX model, or a fake for development before a model exists."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image

# YOLO class order, identical to fridgelens-ai/configs/classes.yaml and the `class` field in
# seed_data/ingredients.yaml. Labels are ingredient slugs.
CLASS_SLUGS = ("apple", "banana", "orange", "lemon", "strawberry", "tomato", "potato", "onion", "garlic",
               "carrot", "cucumber", "bell_pepper", "broccoli", "cauliflower", "cabbage", "leafy_greens",
               "mushroom", "eggplant", "egg", "milk", "cheese", "yogurt", "butter", "chicken", "bread")


@dataclass(frozen=True)
class Detection:
    class_index: int
    label: str
    confidence: float
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class Detector(Protocol):
    name: str
    """Must equal model_versions.name for scans to be linked to the model."""

    def detect(self, image: Image.Image) -> list[Detection]: ...


class FakeDetector:
    """Returns the same labelled boxes for every photo, laid out in a row. For building the app only."""

    name = "fake"

    def __init__(self, spec: str):
        self.items: list[tuple[str, float]] = []
        for part in filter(None, (p.strip() for p in spec.split(","))):
            label, _, conf = part.partition(":")
            if label not in CLASS_SLUGS:
                raise ValueError(f"FAKE_DETECTIONS: unknown class {label!r}")
            self.items.append((label, float(conf or 0.9)))

    def detect(self, image: Image.Image) -> list[Detection]:
        n = max(len(self.items), 1)
        width = 0.8 / n
        return [Detection(CLASS_SLUGS.index(label), label, conf,
                          0.1 + i * width, 0.35, 0.1 + (i + 0.9) * width, 0.65)
                for i, (label, conf) in enumerate(self.items)]


class OnnxDetector:
    """An exported MealLens model: <model>.onnx with model_card.json in the same folder."""

    def __init__(self, model_path: str | Path):
        from .onnx_infer import Detector as OnnxRunner

        model_path = Path(model_path)
        card_path = model_path.with_name("model_card.json")
        if not model_path.exists() or not card_path.exists():
            raise FileNotFoundError(f"need {model_path} and {card_path}; export them with fridgelens-ai")
        card = json.loads(card_path.read_text())
        labels = [c["slug"] for c in sorted(card["classes"], key=lambda c: c["index"])]
        if tuple(labels) != CLASS_SLUGS:
            raise ValueError(f"model classes {labels} do not match the backend's class list")
        self.name = card["name"]
        self.conf = card["conf_threshold"]
        self._runner = OnnxRunner(model_path, card_path)

    def detect(self, image: Image.Image) -> list[Detection]:
        return [Detection(d.class_index, d.label, d.confidence, d.x_min, d.y_min, d.x_max, d.y_max)
                for d in self._runner(image)]


def load_detector(kind: str, model_path: str, fake_spec: str) -> Detector:
    return OnnxDetector(model_path) if kind == "onnx" else FakeDetector(fake_spec)
