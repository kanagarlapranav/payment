import os
import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional, List, Set
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
    or "PYTEST_CURRENT_TEST" in os.environ
)

DEFAULT_PERSISTENT_DATA_DIR = (Path.home() / ".payment_tracker" / "data").resolve()
repo_internal_dir = (BASE_DIR / "data").resolve()
production_dir = DEFAULT_PERSISTENT_DATA_DIR

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
    if resolved_data_dir == repo_internal_dir or repo_internal_dir in resolved_data_dir.parents:
        raise RuntimeError(f"Refusing to run tests against repo data directory: {resolved_data_dir}")
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
    DATA_DIR = Path(raw_data.strip()).resolve() if (raw_data and raw_data.strip()) else DEFAULT_PERSISTENT_DATA_DIR
else:
    raw_data = os.getenv("DATA_DIR")
    DATA_DIR = Path(raw_data.strip()).resolve() if (raw_data and raw_data.strip()) else DEFAULT_PERSISTENT_DATA_DIR

# Loud startup warning if DATA_DIR is inside the repository
if not IS_TEST_ENV and (DATA_DIR == BASE_DIR or BASE_DIR in DATA_DIR.parents or DATA_DIR == repo_internal_dir or repo_internal_dir in DATA_DIR.parents):
    logging.warning(
        "⚠️ CRITICAL PERSISTENCE WARNING: DATA_DIR (%s) is located INSIDE the repository directory (%s)! "
        "A fresh git clone or redeploy WILL WIPE your live SQLite database and stored media! "
        "Please move DATA_DIR outside the repository (default: %s) or set the DATA_DIR environment variable.",
        DATA_DIR, BASE_DIR, DEFAULT_PERSISTENT_DATA_DIR
    )

IMAGE_DIR = DATA_DIR / 'images'
LOG_DIR = Path(os.getenv('LOG_DIR', str(DATA_DIR / 'logs'))).resolve()
DB_PATH = Path(os.getenv('DATABASE_PATH', str(DATA_DIR / 'database.sqlite3'))).resolve()
BACKUP_JSON_PATH = Path(os.getenv('BACKUP_JSON_PATH', str(DATA_DIR / 'backup_transactions.json'))).resolve()

if IS_TEST_ENV:
    if DB_PATH == production_dir / "database.sqlite3" or production_dir in DB_PATH.parents:
        raise RuntimeError(f"Refusing to run tests with DATABASE_PATH pointing inside production data: {DB_PATH}")
    if DB_PATH == repo_internal_dir / "database.sqlite3" or repo_internal_dir in DB_PATH.parents:
        raise RuntimeError(f"Refusing to run tests with DATABASE_PATH pointing inside repo data: {DB_PATH}")
    if BACKUP_JSON_PATH == production_dir / "backup_transactions.json" or production_dir in BACKUP_JSON_PATH.parents:
        raise RuntimeError(f"Refusing to run tests with BACKUP_JSON_PATH pointing inside production data: {BACKUP_JSON_PATH}")
    if BACKUP_JSON_PATH == repo_internal_dir / "backup_transactions.json" or repo_internal_dir in BACKUP_JSON_PATH.parents:
        raise RuntimeError(f"Refusing to run tests with BACKUP_JSON_PATH pointing inside repo data: {BACKUP_JSON_PATH}")
    if LOG_DIR == BASE_DIR / "logs" or production_dir in LOG_DIR.parents:
        raise RuntimeError(f"Refusing to run tests with LOG_DIR pointing inside production/global logs: {LOG_DIR}")

# Ensure directories exist safely
DATA_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Multi-tenant and Legacy Compatibility Flags
LEGACY_SINGLE_TENANT_MODE = os.getenv('LEGACY_SINGLE_TENANT_MODE', 'false').strip().lower() in ('1', 'true', 'yes')
WORKSPACE_MIGRATION_COMPATIBILITY = os.getenv('WORKSPACE_MIGRATION_COMPATIBILITY', 'true').strip().lower() in ('1', 'true', 'yes')
ALLOW_PUBLIC_WORKSPACES = os.getenv('ALLOW_PUBLIC_WORKSPACES', 'false').strip().lower() in ('1', 'true', 'yes')
ALLOW_PUBLIC_WORKSPACE_CREATION = os.getenv('ALLOW_PUBLIC_WORKSPACE_CREATION', 'false').strip().lower() in ('1', 'true', 'yes')
MEMBERSHIP_MODE = os.getenv('MEMBERSHIP_MODE', 'admin_approval').strip().lower()
DEFAULT_MEMBER_ROLE = os.getenv('DEFAULT_MEMBER_ROLE', 'member').strip().lower()
INVITE_EXPIRY_MINUTES = int(os.getenv('INVITE_EXPIRY_MINUTES', '60'))
ACCESS_REQUEST_EXPIRY_HOURS = int(os.getenv('ACCESS_REQUEST_EXPIRY_HOURS', '72'))
DEFAULT_FALLBACK_WORKSPACE_ID = os.getenv('DEFAULT_FALLBACK_WORKSPACE_ID', '').strip()

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Owner User ID (Optional in full multi-tenant mode, mandatory only if LEGACY_SINGLE_TENANT_MODE=true)
raw_user_id = os.getenv('TELEGRAM_USER_ID', '').strip()
if raw_user_id:
    try:
        TELEGRAM_USER_ID = int(raw_user_id)
    except ValueError:
        raise ValueError(f"TELEGRAM_USER_ID must be a valid integer, got: {raw_user_id!r}")
else:
    if LEGACY_SINGLE_TENANT_MODE:
        raise ValueError("TELEGRAM_USER_ID is missing in environment while LEGACY_SINGLE_TENANT_MODE is true.")
    TELEGRAM_USER_ID = None

raw_group_id = os.getenv('TELEGRAM_GROUP_ID', '').strip()
TELEGRAM_GROUP_ID = int(raw_group_id) if raw_group_id and (raw_group_id.isdigit() or (raw_group_id.startswith('-') and raw_group_id[1:].isdigit())) else None

# Optional emergency super-admins for disaster recovery
raw_super_admins = os.getenv('SUPER_ADMIN_USER_IDS', '')
SUPER_ADMIN_USER_IDS = [
    int(uid.strip())
    for uid in raw_super_admins.split(',')
    if uid.strip() and (uid.strip().isdigit() or (uid.strip().startswith('-') and uid.strip()[1:].isdigit()))
]
SUPER_ADMIN_IDS = SUPER_ADMIN_USER_IDS
DEFAULT_FALLBACK_WORKSPACE_ID = os.getenv('DEFAULT_FALLBACK_WORKSPACE_ID', '')

# Restricted users (strictly members only, never permitted owner privileges)
raw_restricted_users = os.getenv('RESTRICTED_USER_IDS', '8343764796')
RESTRICTED_USER_IDS = [
    int(uid.strip())
    for uid in raw_restricted_users.split(',')
    if uid.strip() and (uid.strip().isdigit() or (uid.strip().startswith('-') and uid.strip()[1:].isdigit()))
]

def get_restricted_user_ids(workspace_id=None) -> list[int]:
    """Retrieves restricted user IDs from workspace_settings with fallback to RESTRICTED_USER_IDS."""
    try:
        from database.queries import get_workspace_setting, get_default_workspace_id
        ws_id = workspace_id or get_default_workspace_id()
        db_val = get_workspace_setting(ws_id, 'restricted_user_ids')
        if db_val is not None:
            return [
                int(uid.strip())
                for uid in db_val.split(',')
                if uid.strip() and (uid.strip().isdigit() or (uid.strip().startswith('-') and uid.strip()[1:].isdigit()))
            ]
    except Exception:
        pass
    return list(RESTRICTED_USER_IDS)

def is_restricted_user(user_id, workspace_id=None) -> bool:
    """Checks if a user is in the restricted users list, checking DB with env fallback."""
    if user_id is None:
        return False
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return False
    return uid in get_restricted_user_ids(workspace_id)

def get_default_member_role(workspace_id=None) -> str:
    """Retrieves default member role from workspace_settings with fallback to DEFAULT_MEMBER_ROLE."""
    try:
        from database.queries import get_workspace_setting, get_default_workspace_id
        ws_id = workspace_id or get_default_workspace_id()
        db_val = get_workspace_setting(ws_id, 'default_member_role')
        if db_val:
            return db_val.strip().lower()
    except Exception:
        pass
    return DEFAULT_MEMBER_ROLE

def get_per_transaction_cap(workspace_id=None) -> Optional[float]:
    """Retrieves per-transaction cap from workspace_settings with fallback to PER_TRANSACTION_CAP."""
    try:
        from database.queries import get_workspace_setting, get_default_workspace_id
        ws_id = workspace_id or get_default_workspace_id()
        db_val = get_workspace_setting(ws_id, 'per_tx_cap')
        if db_val is not None and db_val.strip():
            val = float(db_val.strip())
            return val if val > 0 else None
    except Exception:
        pass
    raw = os.getenv('PER_TRANSACTION_CAP', '').strip()
    if raw:
        try:
            val = float(raw)
            return val if val > 0 else None
        except ValueError:
            pass
    return None

def get_monthly_spending_cap(workspace_id=None) -> Optional[float]:
    """Retrieves monthly spending cap from workspace_settings with fallback to MONTHLY_SPENDING_CAP."""
    try:
        from database.queries import get_workspace_setting, get_default_workspace_id
        ws_id = workspace_id or get_default_workspace_id()
        db_val = get_workspace_setting(ws_id, 'monthly_spending_cap')
        if db_val is not None and db_val.strip():
            val = float(db_val.strip())
            return val if val > 0 else None
    except Exception:
        pass
    raw = os.getenv('MONTHLY_SPENDING_CAP', '').strip()
    if raw:
        try:
            val = float(raw)
            return val if val > 0 else None
        except ValueError:
            pass
    return None

def get_gemini_daily_quota(workspace_id=None) -> int:
    """Retrieves Gemini daily quota from workspace_settings with fallback to GEMINI_DAILY_QUOTA."""
    try:
        from database.queries import get_workspace_setting, get_default_workspace_id
        ws_id = workspace_id or get_default_workspace_id()
        db_val = get_workspace_setting(ws_id, 'gemini_daily_quota')
        if db_val is not None and db_val.strip() and db_val.strip().isdigit():
            return int(db_val.strip())
    except Exception:
        pass
    raw = os.getenv('GEMINI_DAILY_QUOTA', '50').strip()
    return int(raw) if raw.isdigit() else 50

def get_quick_add_confirmation(workspace_id=None) -> bool:
    """Retrieves quick-add confirmation toggle from workspace_settings with env fallback."""
    try:
        from database.queries import get_workspace_setting, get_default_workspace_id
        ws_id = workspace_id or get_default_workspace_id()
        db_val = get_workspace_setting(ws_id, 'quick_add_confirmation')
        if db_val is not None:
            return db_val.strip().lower() in ('1', 'true', 'yes', 'on')
    except Exception:
        pass
    return os.getenv('QUICK_ADD_CONFIRMATION', 'true').strip().lower() in ('1', 'true', 'yes', 'on')

# Mapping of restricted user IDs to default (display_name, username)
RESTRICTED_USER_NAMES = {
    8343764796: ("Nagendra", "nagendra")
}


# OCR Configuration: avoid Windows path default on Linux/Render
default_tesseract = r'C:\Program Files\Tesseract-OCR\tesseract.exe' if sys.platform == 'win32' else 'tesseract'
TESSERACT_CMD = os.getenv('TESSERACT_CMD', default_tesseract)
DEFAULT_TIMEZONE = os.getenv('DEFAULT_TIMEZONE', 'Asia/Kolkata')
DAILY_DIGEST_TIME = os.getenv('DAILY_DIGEST_TIME', '22:00')

# Dashboard Security Token for /api/data
DASHBOARD_TOKEN = os.getenv('DASHBOARD_TOKEN', '')
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL', '').rstrip('/')

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
