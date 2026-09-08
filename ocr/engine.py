import os
import signal
import threading
from PIL import Image
from config import TESSERACT_CMD, logger

_rapid_ocr = None
_ocr_lock = threading.Lock()

# Maximum time (seconds) to allow for a single OCR operation
OCR_TIMEOUT_SECONDS = 45


def get_rapid_ocr_engine():
    global _rapid_ocr
    if _rapid_ocr is None:
        with _ocr_lock:
            if _rapid_ocr is None:  # Double-check inside lock
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


def _downscale_if_large(image_path: str, max_dimension: int = 1600) -> str:
    """Downscales images larger than max_dimension to speed up OCR.
    Returns the path to the (possibly resized) image."""
    try:
        img = Image.open(image_path)
        w, h = img.size
        if max(w, h) <= max_dimension:
            return image_path  # Already small enough

        # Calculate scale factor
        scale = max_dimension / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Save resized version
        resized_path = image_path.rsplit('.', 1)
        if len(resized_path) == 2:
            out_path = f"{resized_path[0]}_resized.{resized_path[1]}"
        else:
            out_path = f"{image_path}_resized.jpg"
        img.save(out_path, quality=90)
        logger.info(f"Downscaled image from {w}x{h} to {new_w}x{new_h} for faster OCR")
        return out_path
    except Exception as e:
        logger.warning(f"Could not downscale image: {e}")
        return image_path


def _run_rapidocr(engine, image_path: str) -> str:
    """Runs RapidOCR on the image and returns extracted text."""
    try:
        result, _ = engine(image_path)
        if result:
            lines = [line[1] for line in result if line and len(line) > 1 and line[1].strip()]
            text = "\n".join(lines).strip()
            if text:
                return text
    except Exception as e:
        logger.warning(f"RapidOCR failed on {image_path}: {e}")
    return ""


def extract_text_from_image(image_path: str) -> str:
    """
    Extracts text from the given image using RapidOCR (with pytesseract fallback).
    Downscales large images first for speed. Returns the extracted text.
    """
    resized_path = None
    try:
        # Downscale large images to speed up inference on limited CPU
        resized_path = _downscale_if_large(image_path)
        target_path = resized_path

        # 1. Try RapidOCR first
        engine = get_rapid_ocr_engine()
        if engine is not None:
            text = _run_rapidocr(engine, target_path)
            if text:
                return text

        # 2. Fallback to Tesseract OCR if available
        try:
            import shutil
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
                img = Image.open(target_path)
                custom_config = r'--oem 3 --psm 3'
                text = pytesseract.image_to_string(img, config=custom_config)
                return text.strip()
        except Exception as e:
            logger.error(f"Tesseract OCR Error on {target_path}: {e}")

        return ""
    finally:
        # Clean up resized temp image
        if resized_path and resized_path != image_path and os.path.exists(resized_path):
            try:
                os.remove(resized_path)
            except OSError:
                pass
