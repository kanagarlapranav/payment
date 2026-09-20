import os
import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
IMAGE_DIR = DATA_DIR / 'images'
LOG_DIR = BASE_DIR / 'logs'
DB_PATH = DATA_DIR / 'database.sqlite3'

# Ensure directories exist safely
DATA_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Strictly validate owner User ID as integer to prevent silent auth bypasses
raw_user_id = os.getenv('TELEGRAM_USER_ID')
if not raw_user_id or not raw_user_id.strip():
    raise ValueError("TELEGRAM_USER_ID is missing in environment. Authorization cannot be enforced.")
try:
    TELEGRAM_USER_ID = int(raw_user_id.strip())
except ValueError:
    raise ValueError(f"TELEGRAM_USER_ID must be a valid integer, got: {raw_user_id!r}")

raw_group_id = os.getenv('TELEGRAM_GROUP_ID')
TELEGRAM_GROUP_ID = int(raw_group_id.strip()) if raw_group_id and raw_group_id.strip() else None

# OCR Configuration: avoid Windows path default on Linux/Render
default_tesseract = r'C:\Program Files\Tesseract-OCR\tesseract.exe' if sys.platform == 'win32' else 'tesseract'
TESSERACT_CMD = os.getenv('TESSERACT_CMD', default_tesseract)
DEFAULT_TIMEZONE = 'Asia/Kolkata'

# Dashboard Security Token for /api/data
DASHBOARD_TOKEN = os.getenv('DASHBOARD_TOKEN', '')

# Google Gemini Vision AI Configuration
GEMINI_API_KEY = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.6-flash')

# Google Drive Cloud Storage Configuration
GDRIVE_FOLDER_ID = os.getenv('GDRIVE_FOLDER_ID')
GDRIVE_SERVICE_ACCOUNT_JSON = os.getenv('GDRIVE_SERVICE_ACCOUNT_JSON') # File path or JSON string
GDRIVE_REFRESH_TOKEN = os.getenv('GDRIVE_REFRESH_TOKEN')
GDRIVE_CLIENT_ID = os.getenv('GDRIVE_CLIENT_ID')
GDRIVE_CLIENT_SECRET = os.getenv('GDRIVE_CLIENT_SECRET')

handlers = [logging.StreamHandler()]
try:
    handlers.append(RotatingFileHandler(
        LOG_DIR / 'app.log',
        maxBytes=5 * 1024 * 1024, # 5 MB per file
        backupCount=3,
        encoding='utf-8'
    ))
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=handlers
)
logger = logging.getLogger(__name__)
