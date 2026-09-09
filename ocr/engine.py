import os
import threading
from PIL import Image
from config import TESSERACT_CMD, logger

_rapid_ocr = None
_ocr_lock = threading.Lock()


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
    """Pre-warms the OCR model in a background thread during startup so first request has zero cold-start delay."""
    try:
        engine = get_rapid_ocr_engine()
        if engine:
            import numpy as np
            dummy = np.ones((64, 64, 3), dtype=np.uint8) * 255
            engine(dummy)
            logger.info("OCR model pre-warmed successfully with initial inference.")
    except Exception as e:
        logger.debug(f"OCR warmup notice: {e}")


def _sort_rapid_ocr_boxes(result):
    """
    Sorts RapidOCR detected bounding boxes top-to-bottom, left-to-right.
    Groups text boxes into vertical lines based on vertical overlap.
    """
    if not result:
        return []

    # Box structure: [ [ [x0,y0], [x1,y1], [x2,y2], [x3,y3] ], text, score ]
    items = []
    for item in result:
        if not item or len(item) < 2 or not str(item[1]).strip():
            continue
        box = item[0]
        text = str(item[1]).strip()
        # Compute min/max Y and min X
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        y_min, y_max = min(ys), max(ys)
        x_min = min(xs)
        y_center = (y_min + y_max) / 2.0
        height = max(1.0, y_max - y_min)
        items.append({
            'text': text,
            'ymin': y_min,
            'ymax': y_max,
            'ycenter': y_center,
            'xmin': x_min,
            'height': height
        })

    if not items:
        return []

    # Sort primarily by vertical position (ymin)
    items.sort(key=lambda it: it['ymin'])

    # Group into lines if vertical centers are close (within 50% of box height)
    lines = []
    current_line = []
    current_y = None
    current_h = 20.0

    for it in items:
        if current_y is None:
            current_line = [it]
            current_y = it['ycenter']
            current_h = it['height']
        else:
            # Check if this item belongs to the same horizontal line
            if abs(it['ycenter'] - current_y) <= (current_h * 0.6):
                current_line.append(it)
            else:
                # Flush previous line sorted left-to-right
                current_line.sort(key=lambda x: x['xmin'])
                lines.append(" ".join(x['text'] for x in current_line))
                current_line = [it]
                current_y = it['ycenter']
                current_h = it['height']

    if current_line:
        current_line.sort(key=lambda x: x['xmin'])
        lines.append(" ".join(x['text'] for x in current_line))

    return lines


def extract_text_from_image(image_path: str) -> str:
    """
    Extracts text from the given image using RapidOCR with spatial box sorting,
    multi-pass image enhancement if needed, and pytesseract fallback.
    Returns the extracted text.
    """
    # 1. Try RapidOCR first (works cross-platform via ONNX without external binaries)
    engine = get_rapid_ocr_engine()
    if engine is not None:
        try:
            result, _ = engine(image_path)
            if result:
                lines = _sort_rapid_ocr_boxes(result)
                text = "\n".join(lines).strip()
                if text and len(text) > 10:
                    logger.info(f"RapidOCR extracted {len(text)} chars from {image_path}")
                    return text

            # If initial OCR produced too little text, try with preprocessing
            try:
                from ocr.preprocess import preprocess_image_for_ocr
                proc_path = preprocess_image_for_ocr(image_path)
                if proc_path and proc_path != image_path and os.path.exists(proc_path):
                    res2, _ = engine(proc_path)
                    try:
                        os.remove(proc_path)
                    except OSError:
                        pass
                    if res2:
                        lines2 = _sort_rapid_ocr_boxes(res2)
                        text2 = "\n".join(lines2).strip()
                        if text2:
                            logger.info(f"RapidOCR (preprocessed) extracted {len(text2)} chars")
                            return text2
            except Exception as prep_err:
                logger.debug(f"Preprocessing fallback notice: {prep_err}")

        except Exception as e:
            logger.warning(f"RapidOCR failed on {image_path}: {e}. Falling back to Tesseract.")

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
            img = Image.open(image_path)
            custom_config = r'--oem 3 --psm 6'
            text = pytesseract.image_to_string(img, config=custom_config)
            if text and text.strip():
                return text.strip()
    except Exception as e:
        logger.error(f"Tesseract OCR Error on {image_path}: {e}")

    return ""
