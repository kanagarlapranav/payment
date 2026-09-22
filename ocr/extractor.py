import concurrent.futures
import asyncio
from ocr.engine import extract_text_from_image
from config import logger

_OCR_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="ocr_worker")
_OCR_PIPELINE_TIMEOUT = 30.0


async def perform_ocr_async(image_path: str, timeout: float = _OCR_PIPELINE_TIMEOUT, engine_fn=None) -> str:
    fn = engine_fn or extract_text_from_image
    loop = asyncio.get_running_loop()
    try:
        logger.info(f"Starting OCR on: {image_path} (timeout={timeout}s)")
        result = await asyncio.wait_for(
            loop.run_in_executor(_OCR_EXECUTOR, fn, image_path),
            timeout=timeout
        )
        if result and result.strip():
            logger.info(f"OCR completed successfully ({len(result)} chars extracted).")
            return result
        logger.warning("OCR returned no text from the image.")
        return ""
    except asyncio.TimeoutError:
        logger.warning(f"OCR pipeline timed out after {timeout}s on {image_path}")
        return ""
    except Exception as e:
        logger.error(f"OCR pipeline error on {image_path}: {e}")
        return ""


def perform_ocr(image_path: str, timeout: float = _OCR_PIPELINE_TIMEOUT, engine_fn=None) -> str:
    """
    Synchronous wrapper used by existing code paths.
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
