import os
import time
from pathlib import Path
from config import IMAGE_DIR, logger

def cleanup_old_images(max_age_days: int = 7):
    """Deletes any temporary receipt images older than max_age_days (default 7 days)."""
    try:
        if not IMAGE_DIR.exists():
            return
            
        now = time.time()
        max_age_seconds = max_age_days * 86400
        deleted_count = 0
        
        for item in IMAGE_DIR.iterdir():
            if item.is_file():
                file_age = now - item.stat().st_mtime
                if file_age > max_age_seconds:
                    try:
                        item.unlink()
                        deleted_count += 1
                    except OSError as err:
                        logger.warning(f"Could not delete old image {item}: {err}")
                        
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} image(s) older than {max_age_days} days from {IMAGE_DIR}")
    except Exception as e:
        logger.warning(f"Error during image retention cleanup: {e}")
