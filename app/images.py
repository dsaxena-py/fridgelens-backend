"""Validate and normalise uploaded photos."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 60_000_000  # refuse decompression bombs; a 48 MP phone photo still fits


class InvalidImage(ValueError):
    pass


@dataclass
class PreparedImage:
    image: Image.Image
    jpeg: bytes
    width: int
    height: int


def prepare(data: bytes, max_side: int) -> PreparedImage:
    """Decode, apply the EXIF rotation, convert to RGB, shrink to max_side and re-encode as JPEG.

    Phones store portrait photos sideways with an EXIF rotation tag; applying it here means the
    stored image, the detector input and the box coordinates all share one orientation.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise InvalidImage("not a JPEG or PNG image (HEIC is not supported; ask the camera for JPEG)") from e
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return PreparedImage(img, buf.getvalue(), img.width, img.height)
