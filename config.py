import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Base paths
IS_VERCEL = os.getenv('VERCEL') == '1' or os.getenv('AWS_LAMBDA_FUNCTION_NAME') is not None
BASE_DIR = Path(__file__).resolve().parent

if IS_VERCEL:
    DATA_DIR = Path('/tmp/data')
    IMAGE_DIR = DATA_DIR / 'images'
    LOG_DIR = Path('/tmp/logs')
else:
    DATA_DIR = BASE_DIR / 'data'
    IMAGE_DIR = DATA_DIR / 'images'
    LOG_DIR = BASE_DIR / 'logs'

DB_PATH = DATA_DIR / 'database.sqlite3'

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_USER_ID = os.getenv('TELEGRAM_USER_ID')
TELEGRAM_GROUP_ID = os.getenv('TELEGRAM_GROUP_ID')
TESSERACT_CMD = os.getenv('TESSERACT_CMD', r'C:\Program Files\Tesseract-OCR\tesseract.exe')

# Default Timezone
DEFAULT_TIMEZONE = 'Asia/Kolkata'

# Setup Logging
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

# Validate critical config
if not TELEGRAM_BOT_TOKEN:
    logger.warning("Missing TELEGRAM_BOT_TOKEN environment variable.")
