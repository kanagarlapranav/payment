import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Serverless environments (Vercel/Lambda) must write to /tmp
IS_SERVERLESS = os.getenv('VERCEL') is not None or os.path.exists('/tmp') and os.name != 'nt'
BASE_DIR = Path(__file__).resolve().parent

if IS_SERVERLESS:
    DATA_DIR = Path('/tmp/data')
    IMAGE_DIR = DATA_DIR / 'images'
    LOG_DIR = Path('/tmp/logs')
else:
    DATA_DIR = BASE_DIR / 'data'
    IMAGE_DIR = DATA_DIR / 'images'
    LOG_DIR = BASE_DIR / 'logs'

DB_PATH = DATA_DIR / 'database.sqlite3'


# Ensure directories exist safely
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA_DIR = Path('/tmp/data')
    IMAGE_DIR = DATA_DIR / 'images'
    LOG_DIR = Path('/tmp/logs')
    DB_PATH = DATA_DIR / 'database.sqlite3'
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_USER_ID = os.getenv('TELEGRAM_USER_ID')
TELEGRAM_GROUP_ID = os.getenv('TELEGRAM_GROUP_ID')
TESSERACT_CMD = os.getenv('TESSERACT_CMD', r'C:\Program Files\Tesseract-OCR\tesseract.exe')
DEFAULT_TIMEZONE = 'Asia/Kolkata'

handlers = [logging.StreamHandler()]
try:
    handlers.append(logging.FileHandler(LOG_DIR / 'app.log'))
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=handlers
)
logger = logging.getLogger(__name__)
