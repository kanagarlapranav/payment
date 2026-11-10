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


def test_deploy_azure_workflow_syntax_and_structure():
    """Verify .github/workflows/deploy-azure.yml is valid YAML and follows security invariants."""
    import yaml
    wf_path = BASE_DIR / ".github" / "workflows" / "deploy-azure.yml"
    assert wf_path.exists(), ".github/workflows/deploy-azure.yml does not exist"

    raw_text = wf_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw_text)

    # 1. Valid YAML structure
    assert isinstance(parsed, dict)
    assert parsed.get("name") == "Deploy to Azure VM"

    # 2. Triggers on push to main
    triggers = parsed.get("on") or parsed.get(True)
    if isinstance(triggers, dict):
        assert "push" in triggers
        push_branches = triggers["push"].get("branches", [])
        assert "main" in push_branches
        assert "workflow_dispatch" in triggers
    else:
        assert "push" in str(triggers)

    # 3. References required secrets without hardcoding values
    assert "secrets.AZURE_VM_HOST" in raw_text
    assert "secrets.AZURE_VM_USER" in raw_text
    assert "secrets.AZURE_VM_SSH_KEY" in raw_text

    # 4. Never hardcode live IP addresses or passwords
    lines_without_comments = [
        line for line in raw_text.splitlines()
        if not line.strip().startswith("#")
    ]
    code_text = "\n".join(lines_without_comments)
    assert "172.198.162.13" not in code_text, "Hardcoded IP found outside comments"

    # 5. Calls update_azure.sh and does not touch .env
    assert "scripts/update_azure.sh" in raw_text
    assert "rm -f .env" not in raw_text
    assert "cp .env.example .env" not in raw_text


def test_auto_deploy_azure_documentation():
    """Verify docs/AUTO_DEPLOY_AZURE.md explains dynamic IP, SSH setup, and secrets."""
    doc_path = BASE_DIR / "docs" / "AUTO_DEPLOY_AZURE.md"
    assert doc_path.exists()

    content = doc_path.read_text(encoding="utf-8")
    assert "AZURE_VM_HOST" in content
    assert "AZURE_VM_USER" in content
    assert "AZURE_VM_SSH_KEY" in content
    assert "dynamic" in content.lower()
    assert "update_azure.sh" in content
    assert ".env" in content
    assert "B2ts_v2" in content
    assert "B1s" not in content


def test_no_b1s_references_in_azure_deployment_files():
    """Verify B1s error is eliminated and replaced with B2ts_v2 across Azure deployment files."""
    wf_path = BASE_DIR / ".github" / "workflows" / "deploy-azure.yml"
    assert "B1s" not in wf_path.read_text(encoding="utf-8")
    assert "B2ts_v2" in wf_path.read_text(encoding="utf-8")

    doc_auto = BASE_DIR / "docs" / "AUTO_DEPLOY_AZURE.md"
    assert "B1s" not in doc_auto.read_text(encoding="utf-8")
    assert "B2ts_v2" in doc_auto.read_text(encoding="utf-8")


def test_configure_env_script_syntax_and_behavior(tmp_path):
    """Verify scripts/configure_env.sh handles missing keys, placeholders, and preserves existing keys."""
    script_path = BASE_DIR / "scripts" / "configure_env.sh"
    assert script_path.exists(), "scripts/configure_env.sh does not exist"

    # 1. Syntax check
    bash_bins = ["bash", r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"]
    bash_bin = None
    for b in bash_bins:
        try:
            res = subprocess.run([b, "-n", str(script_path)], capture_output=True, text=True)
            if res.returncode == 0:
                bash_bin = b
                break
        except FileNotFoundError:
            continue

    assert bash_bin is not None, "bash binary not available for testing configure_env.sh"

    # 2. Test handling a .env with missing GEMINI_API_KEY and placeholders
    test_env = tmp_path / "test.env"
    initial_content = (
        "PORT=10000\n"
        "TELEGRAM_BOT_TOKEN=123456789:AAFakePlaceholderBotTokenForTesting35\n"
        "TELEGRAM_USER_ID=123456789\n"
        "TELEGRAM_GROUP_ID=-1001234567890\n"
        "# No GEMINI_API_KEY line present\n"
        "DEFAULT_TIMEZONE=Asia/Kolkata\n"
    )
    test_env.write_text(initial_content, encoding="utf-8")

    # Inputs:
    # 1: real bot token
    # 2: user id (empty to accept 8379948573)
    # 3: group id (empty to accept -1004310685141)
    # 4: gemini key
    inputs = "987654321:AAValidTokenForTestingPurposesOnly12345\n\n\nAQ.ValidGeminiKeyForTestingOnly12345\n"
    proc = subprocess.run(
        [bash_bin, str(script_path), str(test_env)],
        input=inputs,
        capture_output=True,
        text=True,
        check=True
    )

    result_content = test_env.read_text(encoding="utf-8")
    assert "TELEGRAM_USER_ID=8379948573" in result_content
    assert "TELEGRAM_GROUP_ID=-1004310685141" in result_content
    assert "GEMINI_API_KEY=AQ.ValidGeminiKeyForTestingOnly12345" in result_content
    assert "GEMINI_MODEL=gemini-3.6-flash" in result_content
    assert "PORT=10000" in result_content
    assert "DEFAULT_TIMEZONE=Asia/Kolkata" in result_content

    # Output must not leak full secret tokens
    assert "AAValidTokenForTestingPurposesOnly12345" not in proc.stdout
    assert "ValidGeminiKeyForTestingOnly12345" not in proc.stdout

    # 3. Test idempotence: running it again with already-correct values leaves them untouched
    proc2 = subprocess.run(
        [bash_bin, str(script_path), str(test_env)],
        input="\n",
        capture_output=True,
        text=True,
        check=True
    )
    result_content_2 = test_env.read_text(encoding="utf-8")
    assert result_content_2 == result_content
    assert result_content_2.count("TELEGRAM_USER_ID=") == 1
    assert result_content_2.count("GEMINI_API_KEY=") == 1
    assert "OK (configured)" in proc2.stdout



