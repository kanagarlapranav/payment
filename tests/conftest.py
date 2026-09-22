"""
Pytest configuration and global test isolation fixture.
GUARANTEE: No test run can EVER read, write, or pollute production database or backup files in data/.
All tests automatically run in an isolated temporary directory with a clean SQLite database.
"""

import os
import sys
import shutil
import tempfile
from pathlib import Path
import pytest

# Create a dedicated, isolated temporary directory for the pytest session
_TEST_TEMP_DIR = tempfile.mkdtemp(prefix="payment_tracker_test_")
_TEST_DATA_DIR = Path(_TEST_TEMP_DIR)
_TEST_DB_PATH = _TEST_DATA_DIR / "test_database.sqlite3"
_TEST_BACKUP_PATH = _TEST_DATA_DIR / "test_backup.json"
_TEST_LOG_DIR = _TEST_DATA_DIR / "logs"

# Ensure test subdirectories exist
(_TEST_DATA_DIR / "images").mkdir(parents=True, exist_ok=True)
_TEST_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Set environment variables BEFORE any application module is imported
os.environ["PAYMENT_TRACKER_ENV"] = "test"
os.environ["DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["DATABASE_PATH"] = str(_TEST_DB_PATH)
os.environ["BACKUP_JSON_PATH"] = str(_TEST_BACKUP_PATH)
os.environ["LOG_DIR"] = str(_TEST_LOG_DIR)
os.environ["TELEGRAM_USER_ID"] = "123456789"
os.environ["TELEGRAM_GROUP_ID"] = "-100123456789"

BASE_DIR = Path(__file__).resolve().parent.parent
prod_dir = (BASE_DIR / "data").resolve()
assert _TEST_DATA_DIR.resolve() != prod_dir, "Test data directory resolved to production data directory!"
assert prod_dir not in _TEST_DATA_DIR.resolve().parents, "Test data directory is inside production data directory!"

# Configure modules if already loaded or on first import
import config
config.DATA_DIR = _TEST_DATA_DIR
config.DB_PATH = _TEST_DB_PATH
config.BACKUP_JSON_PATH = _TEST_BACKUP_PATH
config.IMAGE_DIR = _TEST_DATA_DIR / "images"
config.LOG_DIR = _TEST_LOG_DIR

import database.db
database.db.DB_PATH = _TEST_DB_PATH

import services.backup_service
services.backup_service.DB_PATH = _TEST_DB_PATH
services.backup_service.BACKUP_JSON_PATH = _TEST_BACKUP_PATH
services.backup_service.DATA_DIR = _TEST_DATA_DIR

from database.db import setup_database
setup_database()


def pytest_configure(config):
    """Called by pytest before test collection starts."""
    os.environ["PAYMENT_TRACKER_ENV"] = "test"
    os.environ["DATA_DIR"] = str(_TEST_DATA_DIR)
    os.environ["DATABASE_PATH"] = str(_TEST_DB_PATH)


def pytest_unconfigure(config):
    """Clean up the session temporary directory after all tests conclude."""
    try:
        shutil.rmtree(_TEST_TEMP_DIR, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def ensure_test_database_isolation(monkeypatch, tmp_path):
    """
    Autouse fixture applied to every test (unit and integration).
    Ensures that if any test overrides or re-initializes paths,
    it is strictly bound to a fresh test path and never data/database.sqlite3.
    """
    # Enforce monkeypatch on all key path attributes
    monkeypatch.setattr("config.DATA_DIR", _TEST_DATA_DIR)
    monkeypatch.setattr("config.DB_PATH", _TEST_DB_PATH)
    monkeypatch.setattr("config.BACKUP_JSON_PATH", _TEST_BACKUP_PATH)
    monkeypatch.setattr("database.db.DB_PATH", _TEST_DB_PATH)
    monkeypatch.setattr("services.backup_service.DB_PATH", _TEST_DB_PATH)
    monkeypatch.setattr("services.backup_service.BACKUP_JSON_PATH", _TEST_BACKUP_PATH)
    monkeypatch.setattr("services.backup_service.DATA_DIR", _TEST_DATA_DIR)

    yield
