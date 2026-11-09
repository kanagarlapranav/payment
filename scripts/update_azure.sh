#!/usr/bin/env bash
# ==============================================================================
# Payment Tracker - Azure VM Fast Update Script
# Usage: Run this script on the VM whenever new commits are pushed to main.
# Never modifies .env or the persistent data directory.
# ==============================================================================
set -euo pipefail

REPO_DIR="${HOME}/payment"
VENV_DIR="${REPO_DIR}/venv"

echo "======================================================================"
echo " Updating Payment Tracker on Azure VM..."
echo "======================================================================"

cd "${REPO_DIR}"

# 1. Pull latest code from GitHub
echo "==> Pulling latest changes from origin/main..."
git pull origin main

# 2. Update Python dependencies if requirements changed
echo "==> Updating dependencies from requirements.txt..."
if [ -f "${VENV_DIR}/bin/pip" ] && [ -f "${REPO_DIR}/requirements.txt" ]; then
    "${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/requirements.txt"
fi

# 3. Restart systemd service
echo "==> Restarting payment-tracker service..."
sudo systemctl restart payment-tracker

# 4. Display service status
echo "==> Checking service status..."
sudo systemctl status payment-tracker --no-pager

echo ""
echo "✅ Update complete! Payment Tracker is running latest code."
