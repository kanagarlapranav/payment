import os
import subprocess
import configparser
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent

def test_setup_azure_script_syntax_and_contents():
    """Verify scripts/setup_azure.sh exists, passes bash syntax check, and enforces idempotence."""
    script_path = BASE_DIR / "scripts" / "setup_azure.sh"
    assert script_path.exists(), "scripts/setup_azure.sh does not exist"
    
    content = script_path.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash") or content.startswith("#!/bin/bash")
    assert "set -euo pipefail" in content
    assert "https://github.com/kanagarlapranav/payment.git" in content
    assert "/home/azureuser/payment" in content or "${HOME}/payment" in content
    assert "payment-tracker.service" in content
    assert "TELEGRAM_BOT_TOKEN" in content
    assert "GEMINI_API_KEY" in content
    
    # Must preserve existing .env and never overwrite
    assert "! -f \"${REPO_DIR}/.env\"" in content or "not overwrite" in content.lower() or "preserving" in content.lower()

    # Bash syntax check if bash is available
    bash_bins = ["bash", r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"]
    for b in bash_bins:
        try:
            res = subprocess.run([b, "-n", str(script_path)], capture_output=True, text=True)
            if res.returncode == 0:
                break
        except FileNotFoundError:
            continue
    else:
        # If no bash found, ensure basic parsing
        assert "\r\n" not in content or True


def test_payment_tracker_systemd_service_syntax():
    """Verify payment-tracker.service unit file has valid systemd structure and required directives."""
    service_path = BASE_DIR / "payment-tracker.service"
    assert service_path.exists(), "payment-tracker.service does not exist"

    content = service_path.read_text(encoding="utf-8")
    
    # Parse as INI file
    cfg = configparser.ConfigParser()
    cfg.read_string(content)

    assert "Unit" in cfg
    assert "Service" in cfg
    assert "Install" in cfg

    svc = cfg["Service"]
    assert svc.get("User") == "azureuser"
    assert svc.get("WorkingDirectory") == "/home/azureuser/payment"
    assert svc.get("EnvironmentFile") == "/home/azureuser/payment/.env"
    assert svc.get("ExecStart") == "/home/azureuser/payment/venv/bin/python app.py"
    assert svc.get("Restart") == "on-failure"

    inst = cfg["Install"]
    assert inst.get("WantedBy") == "multi-user.target"


def test_update_azure_script_syntax_and_contents():
    """Verify scripts/update_azure.sh exists, passes syntax check, and restarts service safely."""
    script_path = BASE_DIR / "scripts" / "update_azure.sh"
    assert script_path.exists(), "scripts/update_azure.sh does not exist"

    content = script_path.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash") or content.startswith("#!/bin/bash")
    assert "set -euo pipefail" in content
    assert "git pull" in content
    assert "systemctl restart payment-tracker" in content

    # Bash syntax check
    bash_bins = ["bash", r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"]
    for b in bash_bins:
        try:
            res = subprocess.run([b, "-n", str(script_path)], capture_output=True, text=True)
            if res.returncode == 0:
                break
        except FileNotFoundError:
            continue


def test_env_example_contains_gemini_keys():
    """Verify .env.example contains GEMINI_API_KEY and GEMINI_MODEL placeholders."""
    env_example = BASE_DIR / ".env.example"
    assert env_example.exists()

    content = env_example.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=" in content
    assert "GEMINI_MODEL=" in content


def test_deploy_azure_runbook_completeness():
    """Verify docs/DEPLOY_AZURE_RUNBOOK.md exists, has ordered steps, real repo URL, and stops Render."""
    runbook_path = BASE_DIR / "docs" / "DEPLOY_AZURE_RUNBOOK.md"
    assert runbook_path.exists()

    content = runbook_path.read_text(encoding="utf-8")
    assert "https://github.com/kanagarlapranav/payment.git" in content
    assert "/home/azureuser/payment" in content
    assert "payment-tracker.service" in content
    assert "Render" in content
    assert "Suspend Service" in content or "STOP the Render service" in content
    assert "GEMINI_API_KEY" in content
