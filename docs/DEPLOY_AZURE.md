# Deploying Payment Tracker to Azure Standard_B2ts_v2 VM (Ubuntu 24.04 / Python 3.13)

This guide provides complete, step-by-step instructions for running Payment Tracker on an Azure Standard_B2ts_v2 Virtual Machine using persistent disk storage under the **Azure for Students** subscription ($100 credit, 365 days).

---

## ⚠️ CRITICAL PREREQUISITE: Stop Render First
Telegram bot tokens support only **one polling connection at a time**.
Before launching the service on your Azure VM:
1. Log in to [dashboard.render.com](https://dashboard.render.com).
2. Navigate to your `payment-tracker-bot` web service.
3. Click **Manual Deploy** / **Settings** and select **Suspend Service** (or stop polling) to ensure only your Azure VM connects to Telegram.

---

## 1. System Setup on Azure VM
Connect to your Ubuntu VM via SSH:
```bash
ssh azureuser@<YOUR_VM_IP>
```

Update system packages and install Python 3.13 and build dependencies:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.13 python3.13-venv python3.13-dev git sqlite3 curl libmagic1
```

---

## 2. Prepare Persistent Storage Directory
Create a dedicated persistent data directory outside the repository so redeploys and git operations never touch your live data:
```bash
sudo mkdir -p /var/lib/payment_tracker/data
sudo mkdir -p /var/lib/payment_tracker/logs
sudo chown -R $USER:$USER /var/lib/payment_tracker
```

---

## 3. Clone Repository & Setup Virtual Environment
```bash
cd ~
git clone https://github.com/<your-username>/payment_tracker.git
cd payment_tracker

python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 4. Configure Environment Variables (`.env`)
Create `.env` in the repository root:
```bash
nano .env
```
Paste your secrets and configure the persistent storage paths:
```env
# Application Environment
PAYMENT_TRACKER_ENV=production
PORT=10000

# Persistent Disk Storage Configuration (Points to persistent VM paths)
DATA_DIR=/var/lib/payment_tracker/data
DATABASE_PATH=/var/lib/payment_tracker/data/database.sqlite3
BACKUP_JSON_PATH=/var/lib/payment_tracker/data/backup_transactions.json
LOG_DIR=/var/lib/payment_tracker/logs

# Telegram Configuration
TELEGRAM_BOT_TOKEN=your_new_telegram_bot_token_here
TELEGRAM_USER_ID=your_telegram_user_id_here
TELEGRAM_GROUP_ID=your_telegram_group_id_here

# Dashboard & Integrations
DASHBOARD_TOKEN=your_dashboard_token_here
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
DEFAULT_TIMEZONE=Asia/Kolkata
DAILY_DIGEST_TIME=22:00
```
Protect file permissions:
```bash
chmod 600 .env
```

---

## 5. Live Data Ingestion
You have two safe methods to place your ledger on the VM:

### Method A (Automatic Cloud Restore — Recommended)
1. Before taking down Render (or via Telegram bot), run `/backup` to create a fresh verified pinned cloud backup.
2. Ensure `/var/lib/payment_tracker/data/database.sqlite3` does not exist on initial start.
3. On first startup, Payment Tracker detects an empty uninitialized database and automatically restores the latest pinned backup from Telegram.

### Method B (Direct Copy via SCP)
If you have an existing `database.sqlite3` file:
```bash
# Run from your local terminal:
scp database.sqlite3 azureuser@<YOUR_VM_IP>:/var/lib/payment_tracker/data/database.sqlite3
```

---

## 6. Setup Systemd Service (Auto-Start & Crash Recovery)
Create the systemd service definition:
```bash
sudo nano /etc/systemd/system/payment-tracker.service
```
Insert the following configuration (replace `azureuser` with your VM username if different):
```ini
[Unit]
Description=Payment Tracker Telegram Bot & Web Service
After=network.target

[Service]
Type=simple
User=azureuser
WorkingDirectory=/home/azureuser/payment_tracker
EnvironmentFile=/home/azureuser/payment_tracker/.env
ExecStart=/home/azureuser/payment_tracker/.venv/bin/python app.py
Restart=always
RestartSec=5s

# Resource and security limits
LimitNOFILE=65536
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Reload systemd, enable, and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable payment-tracker
sudo systemctl start payment-tracker
```

Verify status and startup logs:
```bash
sudo systemctl status payment-tracker
sudo journalctl -u payment-tracker -f
```
Look for the startup confirmation line:
`Storage configuration: DATA_DIR=/var/lib/payment_tracker/data, DB_PATH=/var/lib/payment_tracker/data/database.sqlite3...`

---

## 7. Open Dashboard Port in Azure Network Security Group (NSG)
To access the web dashboard from your browser:
1. Open the [Azure Portal](https://portal.azure.com).
2. Go to **Virtual machines** -> Select your VM -> Click **Networking** (or **Network settings**).
3. Under **Inbound port rules**, click **Add inbound port rule**.
4. Set:
   - **Source:** `Any` (or your specific IP for higher security)
   - **Source port ranges:** `*`
   - **Destination port ranges:** `10000` (or the port defined in `PORT`)
   - **Protocol:** `TCP`
   - **Action:** `Allow`
   - **Priority:** `310`
   - **Name:** `Allow-Payment-Tracker-Dashboard`
5. Click **Add**. You can now access your dashboard at `http://<YOUR_VM_IP>:10000/dashboard`.

