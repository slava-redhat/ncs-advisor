from io import BytesIO
import unittest
from unittest.mock import patch

import pymupdf
from PIL import Image

from advisor.uploads import (
    MAX_UPLOAD_COUNT,
    MAX_VISION_IMAGE_DIMENSION,
    ProcessedUpload,
    UploadProcessingError,
    process_upload,
    process_uploads,
)


def make_pdf(text=None, pages=1):
    document = pymupdf.open()
    for _ in range(pages):
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def make_png():
    image = Image.new("RGB", (240, 120), "white")
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class UploadProcessingTests(unittest.TestCase):
    def test_searchable_pdf_text_does_not_run_ocr(self):
        pdf = make_pdf(
            "NCS diagnostic document contains searchable text and no OCR is needed.")
        with patch("advisor.uploads._ocr_image") as ocr:
            result = process_upload("manual.pdf", pdf)

        self.assertIsInstance(result, ProcessedUpload)
        self.assertIn("searchable text", result.text)
        ocr.assert_not_called()

    def test_scanned_pdf_page_uses_ocr(self):
        pdf = make_pdf()
        with patch("advisor.uploads._ocr_image", return_value="Scanned alarm text") as ocr:
            result = process_upload("scan.pdf", pdf)

        self.assertIn("Page 1\nScanned alarm text", result.text)
        ocr.assert_called_once()

    def test_image_runs_ocr_and_is_available_for_vision(self):
        data = make_png()
        with patch("advisor.uploads._ocr_image", return_value="Node NotReady") as ocr:
            result = process_upload("screen.png", data)

        self.assertEqual(result.text, "Node NotReady")
        self.assertEqual(result.image_attachment, ("screen.png", "image/png", data))
        ocr.assert_called_once()

    def test_large_vision_image_is_resized(self):
        image = Image.new("RGB", (MAX_VISION_IMAGE_DIMENSION + 100, 1200), "white")
        stream = BytesIO()
        image.save(stream, format="PNG")

        with patch("advisor.uploads._ocr_image", return_value=""):
            result = process_upload("large.png", stream.getvalue())

        vision_image = Image.open(BytesIO(result.image_attachment[2]))
        self.assertLessEqual(max(vision_image.size), MAX_VISION_IMAGE_DIMENSION)

    def test_too_many_files_are_rejected_before_processing(self):
        files = [("ignored.png", b"") for _ in range(MAX_UPLOAD_COUNT + 1)]
        with self.assertRaisesRegex(UploadProcessingError, "no more than"):
            process_uploads(files)

    def test_unsupported_file_type_is_rejected(self):
        with self.assertRaisesRegex(UploadProcessingError, "unsupported type"):
            process_upload("notes.txt", b"hello")

    def test_pdf_page_limit_is_enforced(self):
        with self.assertRaisesRegex(UploadProcessingError, "limit is 20"):
            process_upload("large.pdf", make_pdf(pages=21))


if __name__ == "__main__":
    unittest.main()
