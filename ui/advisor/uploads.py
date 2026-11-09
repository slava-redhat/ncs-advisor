"""Local text extraction and OCR for chat image and PDF attachments."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Sequence

import pymupdf
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_MB = 10
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_UPLOAD_COUNT = 3
MAX_PDF_PAGES = 20
MAX_IMAGE_PIXELS = 25_000_000
MAX_EXTRACTED_CHARS = 8_000
MIN_DIGITAL_TEXT_CHARS = 40
MAX_PDF_RENDER_DIMENSION = 3_200
MAX_OCR_DIMENSION = 3_200
MAX_VISION_IMAGE_BYTES = 5 * 1024 * 1024
MAX_VISION_IMAGE_DIMENSION = 2_048

_IMAGE_TYPES = {
    ".jpg": ("image/jpeg", "JPEG"),
    ".jpeg": ("image/jpeg", "JPEG"),
    ".png": ("image/png", "PNG"),
}


class UploadProcessingError(ValueError):
    """An uploaded attachment could not be safely read or OCRed."""


@dataclass(frozen=True)
class ProcessedUpload:
    filename: str
    text: str
    image_attachment: tuple[str, str, bytes] | None = None


def _ocr_image(image: Image.Image) -> str:
    try:
        largest_dimension = max(image.size)
        if largest_dimension < MAX_OCR_DIMENSION:
            scale = min(2.0, MAX_OCR_DIMENSION / largest_dimension)
            image = image.resize(
                (round(image.width * scale), round(image.height * scale)),
                Image.Resampling.LANCZOS,
            )
        prepared = ImageOps.autocontrast(image.convert("L"))
        return pytesseract.image_to_string(prepared, lang="eng").strip()
    except pytesseract.TesseractNotFoundError as exc:
        raise UploadProcessingError("Tesseract OCR is unavailable in the UI container") from exc
    except pytesseract.TesseractError as exc:
        raise UploadProcessingError(f"Tesseract OCR failed: {exc}") from exc


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_EXTRACTED_CHARS:
        return text
    return text[:MAX_EXTRACTED_CHARS].rstrip() + "\n[Text truncated]"


def _vision_image(image: Image.Image, data: bytes, mime_type: str) -> tuple[str, bytes]:
    if len(data) <= MAX_VISION_IMAGE_BYTES and max(image.size) <= MAX_VISION_IMAGE_DIMENSION:
        return mime_type, data

    image = image.copy()
    image.thumbnail(
        (MAX_VISION_IMAGE_DIMENSION, MAX_VISION_IMAGE_DIMENSION),
        Image.Resampling.LANCZOS,
    )
    if mime_type == "image/png":
        stream = BytesIO()
        image.save(stream, format="PNG", optimize=True)
        png_data = stream.getvalue()
        if len(png_data) <= MAX_VISION_IMAGE_BYTES:
            return mime_type, png_data

    for quality in (90, 80, 70):
        stream = BytesIO()
        image.save(stream, format="JPEG", quality=quality, optimize=True)
        jpeg_data = stream.getvalue()
        if len(jpeg_data) <= MAX_VISION_IMAGE_BYTES:
            return "image/jpeg", jpeg_data
    raise UploadProcessingError("Image could not be reduced to the Copilot vision size limit")


def _process_image(filename: str, data: bytes, suffix: str) -> ProcessedUpload:
    try:
        with Image.open(BytesIO(data)) as original:
            mime_type, expected_format = _IMAGE_TYPES[suffix]
            if original.format != expected_format:
                raise UploadProcessingError(
                    f"{filename} does not contain a {expected_format} image")
            if original.width * original.height > MAX_IMAGE_PIXELS:
                raise UploadProcessingError(
                    f"{filename} exceeds the {MAX_IMAGE_PIXELS:,}-pixel image limit")
            image = ImageOps.exif_transpose(original).convert("RGB")
    except UnidentifiedImageError as exc:
        raise UploadProcessingError(f"{filename} is not a readable image") from exc
    except OSError as exc:
        raise UploadProcessingError(f"{filename} could not be opened: {exc}") from exc

    vision_mime, vision_data = _vision_image(image, data, mime_type)
    return ProcessedUpload(
        filename=filename,
        text=_truncate(_ocr_image(image)),
        image_attachment=(filename, vision_mime, vision_data),
    )


def _render_pdf_page(page: pymupdf.Page) -> Image.Image:
    rect = page.rect
    if rect.width <= 0 or rect.height <= 0:
        raise UploadProcessingError("PDF contains a page with invalid dimensions")
    scale = min(2.0, MAX_PDF_RENDER_DIMENSION / max(rect.width, rect.height))
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale),
        colorspace=pymupdf.csRGB,
        alpha=False,
    )
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def _process_pdf(filename: str, data: bytes) -> ProcessedUpload:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, pymupdf.EmptyFileError) as exc:
        raise UploadProcessingError(f"{filename} is not a readable PDF") from exc

    with document:
        if len(document) > MAX_PDF_PAGES:
            raise UploadProcessingError(
                f"{filename} has {len(document)} pages; the limit is {MAX_PDF_PAGES}")

        pages = []
        for page_number, page in enumerate(document, start=1):
            page_text = page.get_text("text").strip()
            digital_chars = sum(char.isalnum() for char in page_text)
            if digital_chars < MIN_DIGITAL_TEXT_CHARS:
                ocr_text = _ocr_image(_render_pdf_page(page))
                page_text = "\n".join(filter(None, (page_text, ocr_text)))
            if page_text:
                pages.append(f"Page {page_number}\n{page_text}")

    return ProcessedUpload(filename=filename, text=_truncate("\n\n".join(pages)))


def process_upload(filename: str, data: bytes) -> ProcessedUpload:
    if not data:
        raise UploadProcessingError(f"{filename} is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadProcessingError(
            f"{filename} exceeds the {MAX_UPLOAD_MB} MB upload limit")

    suffix = Path(filename).suffix.lower()
    if suffix in _IMAGE_TYPES:
        return _process_image(filename, data, suffix)
    if suffix == ".pdf":
        return _process_pdf(filename, data)
    raise UploadProcessingError(
        f"{filename} has an unsupported type; upload a PNG, JPG, or PDF")


def process_uploads(files: Sequence[tuple[str, bytes]]) -> list[ProcessedUpload]:
    if len(files) > MAX_UPLOAD_COUNT:
        raise UploadProcessingError(
            f"Upload no more than {MAX_UPLOAD_COUNT} files per message")
    return [process_upload(filename, data) for filename, data in files]
