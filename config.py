import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Base paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
IMAGE_DIR = DATA_DIR / 'images'
LOG_DIR = BASE_DIR / 'logs'
DB_PATH = DATA_DIR / 'database.sqlite3'

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
IMAGE_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_USER_ID = os.getenv('TELEGRAM_USER_ID')
TELEGRAM_GROUP_ID = os.getenv('TELEGRAM_GROUP_ID')
TESSERACT_CMD = os.getenv('TESSERACT_CMD', r'C:\Program Files\Tesseract-OCR\tesseract.exe')

# Default Timezone
DEFAULT_TIMEZONE = 'Asia/Kolkata'

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Validate critical config
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_USER_ID:
    logger.warning("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_USER_ID in .env file.")
