import concurrent.futures
from ocr.engine import extract_text_from_image
from config import logger

# Module-level executor to prevent blocking on worker exit
_OCR_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="ocr_worker")

# Timeout for the entire OCR pipeline (seconds)
_OCR_PIPELINE_TIMEOUT = 30.0


def perform_ocr(image_path: str, timeout: float = _OCR_PIPELINE_TIMEOUT, engine_fn=None) -> str:
    """
    Orchestrates the OCR pipeline with a hard timeout using a module-level ThreadPoolExecutor.
    Returns the raw extracted text, or "" on timeout / failure.
    """
    fn = engine_fn or extract_text_from_image
    try:
        logger.info(f"Starting OCR on: {image_path} (timeout={timeout}s)")
        future = _OCR_EXECUTOR.submit(fn, image_path)
        text = future.result(timeout=timeout)
        if text and text.strip():
            logger.info(f"OCR completed successfully ({len(text)} chars extracted).")
            return text

        logger.warning("OCR returned no text from the image.")
        return ""
    except concurrent.futures.TimeoutError:
        logger.warning(f"OCR pipeline timed out after {timeout}s on {image_path}")
        return ""
    except Exception as e:
        logger.error(f"OCR pipeline error on {image_path}: {e}")
        return ""
