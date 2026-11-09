#!/usr/bin/env bash
# ==============================================================================
# Payment Tracker - Azure VM One-Command Setup Script
# Target: Ubuntu 24.04 (azureuser on payment-tracker-vm)
# Idempotent: Safe to run on both a fresh VM and a partly set up VM.
# Never overwrites an existing .env or the persistent data directory.
# ==============================================================================
set -euo pipefail

REPO_DIR="${HOME}/payment"
REPO_URL="https://github.com/kanagarlapranav/payment.git"
VENV_DIR="${REPO_DIR}/venv"
DATA_DIR="${HOME}/.payment_tracker/data"
LOG_DIR="${DATA_DIR}/logs"

echo "======================================================================"
echo " Starting Payment Tracker Automated VM Setup..."
echo "======================================================================"

# 1. Update system packages
echo "==> Step 1: Updating system packages..."
sudo apt-get update -y

# 2. Install required packages if missing
echo "==> Step 2: Installing core packages (git, python3-venv, python3-pip)..."
sudo apt-get install -y git python3-venv python3-pip python3 curl sqlite3

# 3. Clone repository if missing, or update if present
echo "==> Step 3: Checking repository at ${REPO_DIR}..."
if [ ! -d "${REPO_DIR}/.git" ]; then
    echo "Cloning repository from ${REPO_URL}..."
    git clone "${REPO_URL}" "${REPO_DIR}"
else
    echo "Repository already exists at ${REPO_DIR}. Pulling latest code..."
    cd "${REPO_DIR}"
    git pull origin main
fi

cd "${REPO_DIR}"

# 4. Create virtual environment if missing
echo "==> Step 4: Checking virtual environment at ${VENV_DIR}..."
if [ ! -d "${VENV_DIR}" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "${VENV_DIR}"
else
    echo "Virtual environment already exists."
fi

# 5. Install and upgrade Python dependencies
echo "==> Step 5: Installing Python dependencies from requirements.txt..."
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/requirements.txt"

# 6. Ensure persistent data and logs directories exist
echo "==> Step 6: Ensuring persistent storage directory exists..."
mkdir -p "${DATA_DIR}" "${LOG_DIR}"
chmod 700 "${DATA_DIR}"

# 7. Create .env from .env.example ONLY if it does not already exist
echo "==> Step 7: Checking .env configuration file..."
if [ ! -f "${REPO_DIR}/.env" ]; then
    echo "Creating ${REPO_DIR}/.env from .env.example..."
    cp "${REPO_DIR}/.env.example" "${REPO_DIR}/.env"
    chmod 600 "${REPO_DIR}/.env"
    echo "Created new .env file. Please edit it with your real credentials."
else
    echo "Existing .env file detected at ${REPO_DIR}/.env. Preserving without changes."
fi

# 8. Summary & Next Steps
echo ""
echo "======================================================================"
echo "  ✅ Setup Completed Successfully!"
echo "======================================================================"
echo ""
echo "Next Steps:"
echo "1. Verify your credentials in ${REPO_DIR}/.env:"
echo "   nano ${REPO_DIR}/.env"
echo "   Confirm TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, and GEMINI_API_KEY are set."
echo ""
echo "2. Install and enable the systemd service (runs on boot & restarts on crash):"
echo "   sudo cp ${REPO_DIR}/payment-tracker.service /etc/systemd/system/payment-tracker.service"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable payment-tracker"
echo "   sudo systemctl start payment-tracker"
echo ""
echo "3. Check service status and live logs:"
echo "   sudo systemctl status payment-tracker"
echo "   sudo journalctl -u payment-tracker -f"
echo ""
echo "4. CRITICAL: Stop the Render service before relying on the Azure VM"
echo "   (Telegram bots support only one polling instance at a time)."
echo "======================================================================"
