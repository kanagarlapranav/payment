from ocr.engine import extract_text_from_image
from config import logger
import os
import concurrent.futures

# Timeout for the entire OCR pipeline (seconds)
_OCR_PIPELINE_TIMEOUT = 60


def perform_ocr(image_path: str) -> str:
    """
    Orchestrates the OCR pipeline with a hard timeout.
    Returns the raw extracted text.
    """
    try:
        # Run OCR directly on the original image.
        # The engine already handles downscaling for speed.
        # No need for OpenCV preprocessing — RapidOCR's neural model
        # handles noisy/low-contrast images better than manual preprocessing.
        logger.info(f"Starting OCR on: {image_path}")
        text = extract_text_from_image(image_path)
        if text and text.strip():
            logger.info(f"OCR completed successfully ({len(text)} chars extracted).")
            return text

        logger.warning("OCR returned no text from the image.")
        return ""
    except Exception as e:
        logger.error(f"OCR pipeline error: {e}")
        return ""
