import os
import sys
import subprocess
import tempfile
from pathlib import Path

def test_config_honors_env_vars():
    """Verifies that config honors DATA_DIR, DATABASE_PATH, BACKUP_JSON_PATH, and LOG_DIR."""
    with tempfile.TemporaryDirectory() as td:
        tmp_base = Path(td).resolve()
        data_d = tmp_base / "custom_data"
        db_p = tmp_base / "custom_db" / "my_database.sqlite3"
        backup_p = tmp_base / "custom_backup" / "my_backup.json"
        log_d = tmp_base / "custom_logs"

        data_d.mkdir(parents=True, exist_ok=True)
        db_p.parent.mkdir(parents=True, exist_ok=True)
        backup_p.parent.mkdir(parents=True, exist_ok=True)
        log_d.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable, "-c",
            "import os, config; "
            "print('DATA_DIR:', config.DATA_DIR); "
            "print('DB_PATH:', config.DB_PATH); "
            "print('BACKUP_JSON_PATH:', config.BACKUP_JSON_PATH); "
            "print('LOG_DIR:', config.LOG_DIR)"
        ]

        env = os.environ.copy()
        env["DATA_DIR"] = str(data_d)
        env["DATABASE_PATH"] = str(db_p)
        env["BACKUP_JSON_PATH"] = str(backup_p)
        env["LOG_DIR"] = str(log_d)
        env["TELEGRAM_BOT_TOKEN"] = "dummy_token"

        res = subprocess.run(cmd, capture_output=True, text=True, env=env, check=True)
        out = res.stdout
        assert f"DATA_DIR: {data_d}" in out
        assert f"DB_PATH: {db_p}" in out
        assert f"BACKUP_JSON_PATH: {backup_p}" in out
        assert f"LOG_DIR: {log_d}" in out


def test_startup_path_warning_when_data_dir_inside_repo():
    """Verifies that a loud warning is emitted when DATA_DIR is inside repository directory in non-test mode."""
    repo_root = Path(__file__).resolve().parent.parent
    inside_repo_data = repo_root / "test_ephemeral_data_dir"

    cmd = [
        sys.executable, "-c",
        "import config"
    ]

    env = os.environ.copy()
    env["PAYMENT_TRACKER_ENV"] = "production"
    env.pop("PYTEST_CURRENT_TEST", None)
    env["DATA_DIR"] = str(inside_repo_data)
    env["DATABASE_PATH"] = str(inside_repo_data / "database.sqlite3")
    env["BACKUP_JSON_PATH"] = str(inside_repo_data / "backup.json")
    env["LOG_DIR"] = str(inside_repo_data / "logs")
    env["TELEGRAM_BOT_TOKEN"] = "dummy_token"

    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    err = res.stderr
    assert "CRITICAL PERSISTENCE WARNING" in err
    assert "located INSIDE the repository directory" in err

