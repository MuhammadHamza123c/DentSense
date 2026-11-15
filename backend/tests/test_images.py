"""Image upload validation tests (pure in-memory, no network)."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from starlette.datastructures import Headers, UploadFile

from app.exceptions import (
    BadRequestError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationAppError,
)
from app.utils.images import read_and_validate_image

MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 50_000_000


def _image_bytes(
    fmt: str = "PNG",
    size: tuple[int, int] = (128, 96),
    color: tuple[int, int, int] = (200, 30, 30),
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def _upload(
    data: bytes,
    *,
    filename: str = "photo.png",
    content_type: str = "image/png",
) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


async def _validate(upload: UploadFile, *, max_bytes: int = MAX_BYTES):
    return await read_and_validate_image(
        upload, max_bytes=max_bytes, max_pixels=MAX_PIXELS
    )


@pytest.mark.parametrize(
    ("fmt", "content_type", "extension"),
    [
        ("JPEG", "image/jpeg", "jpg"),
        ("PNG", "image/png", "png"),
        ("WEBP", "image/webp", "webp"),
    ],
)
async def test_accepts_supported_formats(fmt: str, content_type: str, extension: str) -> None:
    data = _image_bytes(fmt)

    result = await _validate(_upload(data, content_type=content_type))

    assert result.content_type == content_type
    assert result.extension == extension
    assert result.width == 128
    assert result.height == 96
    assert len(result.sha256) == 64
    assert result.size_bytes == len(data)


async def test_accepts_octet_stream_when_bytes_are_valid() -> None:
    result = await _validate(
        _upload(_image_bytes("JPEG"), filename="photo", content_type="application/octet-stream")
    )

    assert result.content_type == "image/jpeg"


async def test_rejects_empty_file() -> None:
    with pytest.raises(BadRequestError):
        await _validate(_upload(b"", content_type="image/png"))


async def test_rejects_files_over_the_limit() -> None:
    with pytest.raises(PayloadTooLargeError):
        await _validate(_upload(b"\x89PNG\r\n\x1a\n" + b"0" * 512), max_bytes=256)


async def test_rejects_non_image_bytes() -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        await _validate(_upload(b"#!/bin/sh\necho hi\n", content_type="image/png"))


async def test_rejects_declared_type_mismatch() -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        await _validate(_upload(_image_bytes("PNG"), content_type="image/jpeg"))


async def test_rejects_disallowed_declared_type() -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        await _validate(_upload(_image_bytes("PNG"), content_type="image/gif"))


async def test_rejects_corrupt_image_with_valid_magic_bytes() -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        await _validate(_upload(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64))


async def test_rejects_too_small_dimensions() -> None:
    with pytest.raises(ValidationAppError):
        await _validate(_upload(_image_bytes("PNG", size=(32, 32))))


async def test_rejects_excessive_pixel_count() -> None:
    upload = _upload(_image_bytes("PNG", size=(128, 96)))

    with pytest.raises(ValidationAppError):
        await read_and_validate_image(upload, max_bytes=MAX_BYTES, max_pixels=1_000)


async def test_upload_stream_is_closed_after_reading() -> None:
    upload = _upload(_image_bytes("PNG"))

    await _validate(upload)

    assert upload.file.closed is True
