# Payment Tracker - Azure VM Deployment Runbook

Follow these 6 steps in exact order to complete your bot migration to the Azure VM (`payment-tracker-vm` at `172.198.162.13`).

---

### Step 1 — Add the missing Gemini key to `.env`
```bash
nano /home/azureuser/payment/.env
```
Open `.env`, scroll to the bottom, add `GEMINI_API_KEY=<your-real-key>` (copied from the `.env` on your local PC, never from chat), and save with `Ctrl+O`, `Enter`, then exit with `Ctrl+X`.

---

### Step 2 — Run the automated setup script
```bash
bash /home/azureuser/payment/scripts/setup_azure.sh
```
This script updates packages, verifies the virtual environment at `/home/azureuser/payment/venv`, installs all dependencies, and creates the persistent data directory `/home/azureuser/.payment_tracker/data` without touching your existing `.env`.

---

### Step 3 — Install and start the systemd service
```bash
sudo cp /home/azureuser/payment/payment-tracker.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now payment-tracker
```
This registers Payment Tracker as a background service that auto-starts on system boot and restarts automatically if it crashes.

---

### Step 4 — Verify the bot is running on the VM
```bash
sudo systemctl status payment-tracker --no-pager
```
Confirm the service status shows `active (running)` and send `/ping` or `/start` to your bot on Telegram to ensure it responds.

---

### Step 5 — STOP the Render service FIRST (Critical Polling Invariant)
```text
https://dashboard.render.com -> Select payment-tracker -> Manual Deploy / Settings -> Suspend Service
```
You must suspend Render now because Telegram allows only one polling instance per bot token at a time; running Render and Azure simultaneously drops incoming messages and causes polling conflicts.

---

### Step 6 — Confirm the bot answers exclusively from Azure
```bash
sudo journalctl -u payment-tracker -n 20 --no-pager
```
Send a test message or command (`/balance`) on Telegram, and verify the journal log shows the request being handled exclusively by your Azure VM.

---

### Future Code Updates
Whenever you push new code to `https://github.com/kanagarlapranav/payment.git`, update your VM in one step:
```bash
bash /home/azureuser/payment/scripts/update_azure.sh
```
This pulls latest code, updates dependencies, and restarts the service without modifying your `.env` or ledger database.
