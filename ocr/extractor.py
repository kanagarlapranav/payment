from ocr.preprocess import preprocess_image_for_ocr
from ocr.engine import extract_text_from_image
from config import logger
import os

def perform_ocr(image_path: str) -> str:
    """
    Orchestrates the OCR pipeline: preprocessing -> text extraction -> cleanup.
    Returns the raw extracted text.
    """
    processed_path = None
    try:
        # First try direct extraction on original (often best for neural OCR models like RapidOCR)
        text = extract_text_from_image(image_path)
        if text.strip():
            logger.info("OCR completed successfully from original image.")
            return text

        # If direct extraction yielded nothing, try preprocessing with OpenCV
        logger.info(f"Preprocessing image: {image_path}")
        processed_path = preprocess_image_for_ocr(image_path)
        
        logger.info("Extracting text from preprocessed image...")
        text = extract_text_from_image(processed_path)
        
        logger.info("OCR completed.")
        return text
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        # Last resort: direct image try if preprocessing raised error
        try:
            return extract_text_from_image(image_path)
        except Exception:
            return ""
    finally:
        # Clean up processed image to save space
        if processed_path and os.path.exists(processed_path):
            try:
                os.remove(processed_path)
            except OSError:
                pass
