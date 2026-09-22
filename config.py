import os
import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# DATA_DIR can be overridden via environment variable (e.g. DATA_DIR=/var/data or C:\data).
# Note on SQLite WAL Reliability: SQLite Write-Ahead Logging (-wal) mode uses companion shared-memory
# (-shm) and write-ahead log (-wal) files. When databases are placed inside cloud-synced folders
# (such as Microsoft OneDrive, Dropbox, or Google Drive for Desktop), the background file synchronization
# engines periodically lock or asynchronously copy these ephemeral files, causing spurious
# 'database is locked' errors, disk I/O latency, or potential database corruption. Setting DATA_DIR
# to a local, non-cloud-synced directory avoids these locking hazards.
ENV_NAME = os.getenv("PAYMENT_TRACKER_ENV", "").strip().lower()

IS_TEST_ENV = (
    os.getenv("PAYMENT_TRACKER_ENV", "").strip().lower() == "test"
    or "pytest" in sys.modules
)

production_dir = (BASE_DIR / "data").resolve()

if IS_TEST_ENV:
    if not os.getenv("DATA_DIR"):
        raise RuntimeError("Test mode requires DATA_DIR to point to an isolated temporary directory.")
    if not os.getenv("DATABASE_PATH"):
        raise RuntimeError("Test mode requires DATABASE_PATH to point to an isolated temporary directory.")
    if not os.getenv("LOG_DIR"):
        raise RuntimeError("Test mode requires LOG_DIR to point to an isolated temporary directory.")

    resolved_data_dir = Path(os.environ["DATA_DIR"]).resolve()
    if resolved_data_dir == production_dir or production_dir in resolved_data_dir.parents:
        raise RuntimeError(f"Refusing to run tests against production data directory: {resolved_data_dir}")
    DATA_DIR = resolved_data_dir
elif ENV_NAME == "development":
    raw_data = os.getenv("DATA_DIR")
    if raw_data and raw_data.strip():
        DATA_DIR = Path(raw_data.strip()).resolve()
        if DATA_DIR == production_dir or production_dir in DATA_DIR.parents:
            raise RuntimeError("Development mode refusing to point to production data directory")
    else:
        DATA_DIR = (BASE_DIR / "devdata").resolve()
elif ENV_NAME == "production":
    raw_data = os.getenv("DATA_DIR")
    DATA_DIR = Path(raw_data.strip()).resolve() if (raw_data and raw_data.strip()) else production_dir
else:
    raw_data = os.getenv("DATA_DIR")
    DATA_DIR = Path(raw_data.strip()).resolve() if (raw_data and raw_data.strip()) else production_dir

IMAGE_DIR = DATA_DIR / 'images'
LOG_DIR = Path(os.getenv('LOG_DIR', str(DATA_DIR / 'logs'))).resolve()
DB_PATH = Path(os.getenv('DATABASE_PATH', str(DATA_DIR / 'database.sqlite3'))).resolve()
BACKUP_JSON_PATH = Path(os.getenv('BACKUP_JSON_PATH', str(DATA_DIR / 'backup_transactions.json'))).resolve()

if IS_TEST_ENV:
    if DB_PATH == production_dir / "database.sqlite3" or production_dir in DB_PATH.parents:
        raise RuntimeError(f"Refusing to run tests with DATABASE_PATH pointing inside production data: {DB_PATH}")
    if BACKUP_JSON_PATH == production_dir / "backup_transactions.json" or production_dir in BACKUP_JSON_PATH.parents:
        raise RuntimeError(f"Refusing to run tests with BACKUP_JSON_PATH pointing inside production data: {BACKUP_JSON_PATH}")
    if LOG_DIR == BASE_DIR / "logs" or production_dir in LOG_DIR.parents:
        raise RuntimeError(f"Refusing to run tests with LOG_DIR pointing inside production/global logs: {LOG_DIR}")

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
DEFAULT_TIMEZONE = os.getenv('DEFAULT_TIMEZONE', 'Asia/Kolkata')
DAILY_DIGEST_TIME = os.getenv('DAILY_DIGEST_TIME', '22:00')

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

import re

class SensitiveDataFilter(logging.Filter):
    """
    Masks sensitive credentials (Telegram bot tokens, Google AIza keys)
    in log messages and formatting arguments across all handlers and loggers.
    """
    _TELEGRAM_TOKEN_RE = re.compile(r'\b\d{8,11}:AA[A-Za-z0-9_-]{33}\b')
    _AIZA_KEY_RE = re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b')

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.mask_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (self.mask_secrets(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self.mask_secrets(v) if isinstance(v, str) else v
                    for v in record.args
                )
            elif isinstance(record.args, list):
                record.args = [
                    self.mask_secrets(v) if isinstance(v, str) else v
                    for v in record.args
                ]
        return True

    @classmethod
    def mask_secrets(cls, text: str) -> str:
        if not text or not isinstance(text, str):
            return text
        text = cls._TELEGRAM_TOKEN_RE.sub('[REDACTED_TELEGRAM_TOKEN]', text)
        text = cls._AIZA_KEY_RE.sub('[REDACTED_API_KEY]', text)
        return text

sensitive_filter = SensitiveDataFilter()
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

for h in handlers:
    h.addFilter(sensitive_filter)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=handlers
)

# Suppress verbose HTTP loggers that can leak Telegram bot tokens in request URLs
for noisy_logger_name in ('httpx', 'httpcore', 'urllib3', 'telegram'):
    logging.getLogger(noisy_logger_name).setLevel(logging.WARNING)

logging.getLogger().addFilter(sensitive_filter)
logger = logging.getLogger(__name__)
