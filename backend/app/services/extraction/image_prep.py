"""Prepare an uploaded invoice file for the vision LLM.

Per docs/EXTRACTION_CONTRACT.md "Image preparation": HEIC -> JPEG,
PDF pages -> images (capped at 5, flagging truncation), EXIF rotation
applied, long edge downscaled to 2048px. Returns one JPEG per page/
image, ready to send to the model.
"""

from __future__ import annotations

import io

import pillow_heif
import pymupdf as fitz
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()

MAX_PAGES = 5
MAX_LONG_EDGE = 2048
JPEG_QUALITY = 95
PDF_RENDER_DPI = 200

SUPPORTED_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/heic", "application/pdf"}
)


class UnsupportedContentType(ValueError):
    pass


class CorruptFile(ValueError):
    pass


def _finish(image: Image.Image) -> bytes:
    """EXIF-rotate, downscale, re-encode as JPEG."""
    image = ImageOps.exif_transpose(image) or image
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    long_edge = max(image.size)
    if long_edge > MAX_LONG_EDGE:
        scale = MAX_LONG_EDGE / long_edge
        new_size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def _render_pdf(content: bytes) -> tuple[list[bytes], list[str]]:
    flags: list[str] = []
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises various fitz-specific errors
        raise CorruptFile(f"could not open PDF: {exc}") from exc

    if doc.needs_pass:
        raise CorruptFile("PDF is password-protected")

    page_count = doc.page_count
    pages_to_render = min(page_count, MAX_PAGES)
    if page_count > MAX_PAGES:
        flags.append("multi_page_truncated")

    zoom = PDF_RENDER_DPI / 72
    matrix = fitz.Matrix(zoom, zoom)
    images: list[bytes] = []
    for i in range(pages_to_render):
        pix = doc[i].get_pixmap(matrix=matrix)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        images.append(_finish(img))
    doc.close()
    return images, flags


def prepare_images(
    content: bytes, content_type: str
) -> tuple[list[bytes], list[str]]:
    """Return (jpeg_pages, flags) ready for the LLM.

    Raises `UnsupportedContentType` / `CorruptFile` for inputs the
    worker should fail the ingestion for rather than call the LLM.
    """
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise UnsupportedContentType(content_type)

    if content_type == "application/pdf":
        return _render_pdf(content)

    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except Exception as exc:
        raise CorruptFile(f"could not open image: {exc}") from exc
    return [_finish(image)], []
