"""Unit tests for image_prep: HEIC/PDF/JPEG -> prepared JPEG pages."""

from __future__ import annotations

import io

import pymupdf
import pytest
from app.services.extraction.image_prep import (
    MAX_LONG_EDGE,
    CorruptFile,
    UnsupportedContentType,
    prepare_images,
)
from PIL import Image


def _jpeg_bytes(size: tuple[int, int] = (100, 60)) -> bytes:
    img = Image.new("RGB", size, color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _png_bytes(size: tuple[int, int] = (100, 60)) -> bytes:
    img = Image.new("RGB", size, color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pdf_bytes(pages: int) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((50, 50), "invoice")
    content = doc.tobytes()
    doc.close()
    return content


def test_jpeg_passthrough_returns_one_page() -> None:
    images, flags = prepare_images(_jpeg_bytes(), "image/jpeg")
    assert len(images) == 1
    assert flags == []
    # output is itself a valid JPEG
    Image.open(io.BytesIO(images[0])).load()


def test_png_converted_to_jpeg() -> None:
    images, flags = prepare_images(_png_bytes(), "image/png")
    assert len(images) == 1
    out = Image.open(io.BytesIO(images[0]))
    assert out.format == "JPEG"


def test_downscales_long_edge() -> None:
    images, _ = prepare_images(_jpeg_bytes(size=(4000, 1000)), "image/jpeg")
    out = Image.open(io.BytesIO(images[0]))
    assert max(out.size) <= MAX_LONG_EDGE


def test_pdf_single_page() -> None:
    images, flags = prepare_images(_pdf_bytes(1), "application/pdf")
    assert len(images) == 1
    assert flags == []


def test_pdf_multi_page_under_cap_not_truncated() -> None:
    images, flags = prepare_images(_pdf_bytes(3), "application/pdf")
    assert len(images) == 3
    assert flags == []


def test_pdf_over_cap_truncated_and_flagged() -> None:
    images, flags = prepare_images(_pdf_bytes(7), "application/pdf")
    assert len(images) == 5
    assert "multi_page_truncated" in flags


def test_unsupported_content_type_rejected() -> None:
    with pytest.raises(UnsupportedContentType):
        prepare_images(b"whatever", "text/plain")


def test_corrupt_image_rejected() -> None:
    with pytest.raises(CorruptFile):
        prepare_images(b"not a real image", "image/jpeg")


def test_corrupt_pdf_rejected() -> None:
    with pytest.raises(CorruptFile):
        prepare_images(b"not a real pdf", "application/pdf")
