# ⚡ Personal Payment Tracker Telegram Bot

A 24/7 autonomous financial companion and personal ledger bot built on Telegram. Automatically extracts and records UPI payment receipts (via **Google Gemini Vision AI API** with multi-model fallback & **RapidOCR** backup), tracks real-time account balances, manages retroactive edits with dynamic recalculation, provides an intelligent **Pure Vegetarian Cafeteria Menu & Spending Tracker**, automated daily digests, proactive budget tracking, dark-mode web analytics dashboard, complete undo management, and exports professional PDF/Excel statements.

---

## 🏗️ Architecture & Core Components

- **Vision & OCR Pipeline**: Google Gemini Vision AI (`gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-flash-latest`) with automatic fallback to embedded **RapidOCR** (ONNX Runtime). *No external Tesseract installation required.*
- **Immutable Ledger & Double-Entry Math**:
  - Sequence-validated SQLite ledger with SHA-256 integrity hash chaining (`previous_hash` + `current_hash`).
  - Strict input validation rejecting NaN, Inf, zero, or negative amounts.
  - Soft-delete architecture with tombstones (`deleted_at`) preserving balance recalculation integrity and preventing resurrected rows.
  - Reversible multi-level undo history via `ledger_undo_log`.
- **Cloud Backup & Disaster Recovery (Backup Format v2)**:
  - Atomic JSON backups with SHA-256 manifest checksums, database schema versioning, and sequence integrity checks.
  - Periodic 60-second dirty sync and graceful shutdown backup hooks.
  - Automatic restore on fresh/wiped instances: restores only when the database is completely empty and uninitialized. If restore fails on an empty database, sets `backup_blocked=true` to prevent wiping cloud state until valid data is entered.
  - Empty-ledger backup creation when all rows are intentionally deleted.
- **JobQueue Scheduler**:
  - Uses `python-telegram-bot` `JobQueue` with `ZoneInfo("Asia/Kolkata")`.
  - Daily Financial Digest sent automatically at `DAILY_DIGEST_TIME` (default: `22:00` IST) with 3-attempt exponential retry and idempotent sent tracking.
  - Periodic dirty backup retry job (every 60s) and tombstone purge maintenance job (every 24h).
- **Security & Authorization**:
  - Two-tier access control: **Owner-Only** (mutations, edits, deletes, balance updates, exports, backups, restore, dashboard, settings) vs. **Group Members** (read-only views: balance, today, history, stats, cafeteria menu).
  - Protected web dashboard utilizing 60-second single-use one-time login codes exchanged via `/auth?code=...` for `HttpOnly`, `SameSite=Strict`, `Secure` 30-minute session cookies.
  - Security headers: `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` on API responses.
  - Strict HTML escaping on all dynamic variables in Telegram outputs to prevent entity injection attacks.

---

## ⚙️ Setup & Installation

### 1. Requirements
- Python 3.13+
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- Google Gemini API Key from [Google AI Studio](https://aistudio.google.com/)

### 2. Local Setup
```bash
# Clone the repository
git clone https://github.com/kanagarlapranav/payment.git
cd payment

# Create and activate virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env` and fill in your credentials:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_USER_ID=your_numeric_user_id
TELEGRAM_GROUP_ID=-1001234567890
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.6-flash
DEFAULT_TIMEZONE=Asia/Kolkata
DAILY_DIGEST_TIME=22:00
PORT=10000
RENDER_EXTERNAL_URL=https://your-service-name.onrender.com
```

### 4. Running the Bot
```bash
python app.py
```

---

## 🧪 Testing & Validation Commands

Run the full automated test suite (330+ unit and integration tests):
```bash
# Run all unit and integration tests
pytest -v

# Run with test coverage report
pytest --cov=. --cov-report=term-missing

# Run bytecode compilation check
python -m compileall .

# Verify database ledger integrity
python -m tools.check_ledger

# Verify backup file checksum and sequence integrity
python -m tools.verify_backup data/backup_v2.json
```

---

## 🔄 Database Migrations & Tools

- **Migrate v1 Backup to v2 Schema**:
  ```bash
  python -m tools.migrate_v1_to_v2 data/backup_v1.json data/backup_v2.json
  ```
- **Ledger Resequence & Recalculation**:
  ```bash
  python -m tools.resequence_ledger
  ```

---

## ☁️ Render Deployment & Ephemeral Disk Recovery

### Render Deployment Configuration
- Render Web Service configured via [render.yaml](file:///C:/Users/prana/OneDrive/Desktop/payment_tracker/render.yaml) with `startCommand: python app.py` and `PYTHON_VERSION: 3.13.14`.
- Procfile is synchronized with `web: python app.py`.

### Ephemeral Disk Recovery Procedure
If Render restarts or wipes its ephemeral storage disk:
1. On startup, [`on_startup`](file:///C:/Users/prana/OneDrive/Desktop/payment_tracker/app.py#L26-L98) detects an empty database (`total_rows == 0` and `database_initialized == false`).
2. It automatically requests the latest valid v2 JSON backup from the Telegram Cloud backup channel / pinned messages.
3. If Telegram cloud backup is unreachable, it falls back to the local `data/backup_v2.json` (verifying SHA-256 checksums and sequence integrity).
4. If neither is available, it sets `backup_blocked=true` to prevent uploading blank databases until the user manually restores or inputs the first verified transaction.

---

## 🔐 Manual Credential Rotation Procedure

If API keys or tokens are ever compromised:
1. **Telegram Bot Token**:
   - Open Telegram -> `@BotFather` -> `/revoke` -> Select bot -> Generate new token.
   - Update `TELEGRAM_BOT_TOKEN` in `.env` and Render Environment Variables.
   - Restart the application.
2. **Google Gemini API Key**:
   - Visit [Google AI Studio](https://aistudio.google.com/) -> API Keys -> Delete old key -> Create new key.
   - Update `GEMINI_API_KEY` in `.env` and Render Environment Variables.
3. **Google Drive Credentials**:
   - Go to Google Cloud Console -> APIs & Services -> Credentials -> Reset Client Secret or generate new Service Account Key.
   - Update `GDRIVE_CLIENT_SECRET` / `GDRIVE_REFRESH_TOKEN` / `GDRIVE_SERVICE_ACCOUNT_JSON` in `.env` and Render.
