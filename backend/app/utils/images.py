"""Image upload validation (type, magic bytes, decodability, size, dimensions)."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.exceptions import (
    BadRequestError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationAppError,
)

ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
ALLOWED_EXTENSIONS = frozenset(ALLOWED_CONTENT_TYPES.values())
_MIN_DIMENSION = 64

# Pillow raises DecompressionBombError above this many pixels.
Image.MAX_IMAGE_PIXELS = 60_000_000


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    """An image that passed every upload check."""

    data: bytes
    content_type: str
    extension: str
    width: int
    height: int
    sha256: str

    @property
    def size_bytes(self) -> int:
        return len(self.data)


def _detect_magic(data: bytes) -> tuple[str, str] | None:
    if len(data) < 12:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


async def read_and_validate_image(
    upload: UploadFile, *, max_bytes: int, max_pixels: int
) -> ValidatedImage:
    """Read an upload with a hard byte cap and verify it is a usable image.

    Raises:
        BadRequestError: empty file.
        PayloadTooLargeError: file exceeds ``max_bytes``.
        UnsupportedMediaTypeError: wrong type / corrupt / animated / mismatched bytes.
        ValidationAppError: too small or too many pixels.
    """
    try:
        data = await upload.read(max_bytes + 1)
    finally:
        await upload.close()

    if not data:
        raise BadRequestError("The uploaded file is empty.")
    if len(data) > max_bytes:
        raise PayloadTooLargeError(
            f"Image exceeds the {max_bytes // (1024 * 1024)} MB upload limit."
        )

    detected = _detect_magic(data)
    if detected is None:
        raise UnsupportedMediaTypeError(
            "Only JPG, JPEG, PNG and WEBP images are supported."
        )
    detected_type, extension = detected

    declared = (upload.content_type or "").lower().strip()
    if declared and declared not in {"application/octet-stream", "binary/octet-stream"}:
        if declared not in ALLOWED_CONTENT_TYPES:
            raise UnsupportedMediaTypeError(
                "Only JPG, JPEG, PNG and WEBP images are supported."
            )
        if ALLOWED_CONTENT_TYPES[declared] != extension:
            raise UnsupportedMediaTypeError(
                "The file content does not match its declared content type."
            )

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            frames = int(getattr(image, "n_frames", 1))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnsupportedMediaTypeError(
            "The image is corrupt or could not be decoded."
        ) from exc
    except Image.DecompressionBombError as exc:  # pragma: no cover - Pillow safety net
        raise ValidationAppError("The image dimensions are too large.") from exc

    if frames > 1:
        raise UnsupportedMediaTypeError("Animated images are not supported.")
    if width < _MIN_DIMENSION or height < _MIN_DIMENSION:
        raise ValidationAppError(
            f"Image must be at least {_MIN_DIMENSION}x{_MIN_DIMENSION} pixels."
        )
    if width * height > max_pixels:
        raise ValidationAppError(
            "The image has too many pixels. Please upload a smaller photo."
        )

    return ValidatedImage(
        data=data,
        content_type=detected_type,
        extension=extension,
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
    )
