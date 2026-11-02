"""Copied from fridgelens-ai/meallens_ai/onnx_infer.py; keep the two in sync.

Run an exported MealLens ONNX model with NumPy + Pillow + ONNX Runtime only (no PyTorch).

This is the reference inference path for the FastAPI backend: letterbox -> model -> confidence
filter -> class-aware NMS -> boxes normalised to the original image, matching the
detections table (x_min, y_min, x_max, y_max in [0, 1]).

The export is Ultralytics' raw head: output shape (1, 4 + num_classes, N) with cx, cy, w, h in
input pixels followed by per-class scores.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


@dataclass(frozen=True)
class Detection:
    class_index: int
    label: str
    confidence: float
    x_min: float
    y_min: float
    x_max: float
    y_max: float


def letterbox(img: Image.Image, size: int) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Resize keeping aspect ratio and pad to size x size with grey (114), like Ultralytics."""
    w, h = img.size
    scale = min(size / w, size / h)
    nw, nh = round(w * scale), round(h * scale)
    resized = img.resize((nw, nh), Image.BILINEAR)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    pad = ((size - nw) // 2, (size - nh) // 2)
    canvas.paste(resized, pad)
    arr = np.asarray(canvas, dtype=np.float32) / 255.0
    return arr.transpose(2, 0, 1)[None], scale, pad


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float) -> list[int]:
    """Indices kept by greedy NMS; boxes are xyxy."""
    order = scores.argsort()[::-1]
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou < iou_thr]
    return keep


def decode(output: np.ndarray, conf: float, iou_thr: float, max_det: int = 300) -> np.ndarray:
    """Raw (1, 4 + nc, N) output -> array of [x1, y1, x2, y2, score, class] in input pixels."""
    pred = output[0].T  # (N, 4 + nc)
    scores_all = pred[:, 4:]
    cls = scores_all.argmax(1)
    score = scores_all[np.arange(len(cls)), cls]
    mask = score >= conf
    if not mask.any():
        return np.zeros((0, 6), dtype=np.float32)
    xywh, score, cls = pred[mask, :4], score[mask], cls[mask]
    xyxy = np.concatenate([xywh[:, :2] - xywh[:, 2:] / 2, xywh[:, :2] + xywh[:, 2:] / 2], axis=1)
    # Class-aware NMS: offset each class so boxes of different classes never suppress each other.
    offset = cls[:, None].astype(np.float32) * 4096.0
    keep = nms(xyxy + offset, score, iou_thr)[:max_det]
    return np.concatenate([xyxy[keep], score[keep, None], cls[keep, None].astype(np.float32)], axis=1)


class Detector:
    def __init__(self, onnx_path: str | Path, card_path: str | Path | None = None,
                 conf: float | None = None, iou: float | None = None, providers: list[str] | None = None):
        import onnxruntime as ort

        onnx_path = Path(onnx_path)
        card_path = Path(card_path) if card_path else onnx_path.with_name("model_card.json")
        card = json.loads(card_path.read_text())
        self.labels = [c["slug"] for c in sorted(card["classes"], key=lambda c: c["index"])]
        self.size = int(card["input_size"])
        self.conf = conf if conf is not None else float(card["conf_threshold"])
        self.iou = iou if iou is not None else float(card["iou_threshold"])
        self.session = ort.InferenceSession(str(onnx_path), providers=providers or ["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def __call__(self, image: Image.Image | str | Path) -> list[Detection]:
        img = image if isinstance(image, Image.Image) else Image.open(image)
        img = ImageOps.exif_transpose(img).convert("RGB")  # phone photos carry their rotation in EXIF
        w, h = img.size
        tensor, scale, (px, py) = letterbox(img, self.size)
        output = self.session.run(None, {self.input_name: tensor})[0]
        dets = decode(output, self.conf, self.iou)
        results = []
        for x1, y1, x2, y2, s, c in dets:
            box = [(x1 - px) / scale / w, (y1 - py) / scale / h, (x2 - px) / scale / w, (y2 - py) / scale / h]
            box = [min(1.0, max(0.0, float(v))) for v in box]
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            results.append(Detection(int(c), self.labels[int(c)], float(s), *box))
        return results
