import os
import shutil
from PIL import Image
from config import TESSERACT_CMD, logger

_rapid_ocr = None

def get_rapid_ocr_engine():
    global _rapid_ocr
    if _rapid_ocr is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _rapid_ocr = RapidOCR()
            logger.info("RapidOCR engine initialized successfully.")
        except Exception as e:
            logger.warning(f"Could not initialize RapidOCR: {e}")
            _rapid_ocr = False
    return _rapid_ocr if _rapid_ocr is not False else None

def warmup_ocr():
    """Pre-warms the OCR model in a background thread during startup."""
    try:
        get_rapid_ocr_engine()
    except Exception as e:
        logger.debug(f"OCR warmup notice: {e}")

def extract_text_from_image(image_path: str) -> str:
    """
    Extracts text from the given image using RapidOCR (with pytesseract fallback).
    Returns the extracted text.
    """
    # 1. Try RapidOCR first (works cross-platform via ONNX without external binaries)
    engine = get_rapid_ocr_engine()
    if engine is not None:
        try:
            result, _ = engine(image_path)
            if result:
                lines = [line[1] for line in result if line and len(line) > 1 and line[1].strip()]
                text = "\n".join(lines).strip()
                if text:
                    return text
        except Exception as e:
            logger.warning(f"RapidOCR failed on {image_path}: {e}. Falling back to Tesseract.")

    # 2. Fallback to Tesseract OCR if available
    try:
        tesseract_bin = None
        if os.name == 'nt' and os.path.exists(TESSERACT_CMD):
            tesseract_bin = TESSERACT_CMD
        elif shutil.which('tesseract'):
            tesseract_bin = 'tesseract'
        elif os.path.exists(TESSERACT_CMD):
            tesseract_bin = TESSERACT_CMD

        if tesseract_bin:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = tesseract_bin
            img = Image.open(image_path)
            custom_config = r'--oem 3 --psm 3'
            text = pytesseract.image_to_string(img, config=custom_config)
            return text.strip()
    except Exception as e:
        logger.error(f"Tesseract OCR Error on {image_path}: {e}")

    return ""
