import os
from PIL import Image
from config import TESSERACT_CMD, logger

_rapid_ocr = None

def get_rapid_ocr_engine():
    global _rapid_ocr
    if _rapid_ocr is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _rapid_ocr = RapidOCR()
        except Exception as e:
            logger.warning(f"Could not initialize RapidOCR: {e}")
            _rapid_ocr = False
    return _rapid_ocr if _rapid_ocr is not False else None

def extract_text_from_image(image_path: str) -> str:
    """
    Extracts text from the given image using RapidOCR (with pytesseract fallback).
    Returns the extracted text.
    """
    # 1. Try RapidOCR first (works locally via ONNX without external dependencies)
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
        import pytesseract
        if os.path.exists(TESSERACT_CMD) or os.name != 'nt':
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
            img = Image.open(image_path)
            custom_config = r'--oem 3 --psm 3'
            text = pytesseract.image_to_string(img, config=custom_config)
            return text.strip()
    except Exception as e:
        logger.error(f"Tesseract OCR Error on {image_path}: {e}")

    return ""
