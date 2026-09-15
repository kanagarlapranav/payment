# Payment Tracker Telegram Bot — Project Summary & Architecture Guide
**Last Updated**: 15 Sep 2026

A 24/7 autonomous financial companion and personal ledger bot built on Telegram. Automatically extracts and records UPI payment receipts (via **Gemini 3.6 Flash Vision AI** & **RapidOCR**), tracks real-time account balances, manages retroactive edits with dynamic recalculation, and exports professional PDF/Excel statements.

---

## 🚀 Live Production Status

- **Status**: Active & Live (Production)
- **Deployment URL**: `https://payment-3-kldp.onrender.com`
- **Repository**: `https://github.com/kanagarlapranav/payment.git` (Branch: `main`)
- **Runtime**: Python 3.13 / `python-telegram-bot` (v21+ AsyncIO)
- **Deployment Platform**: Render Web Service (24/7 Always-On Background Polling + HTTP Self-Pinger)

---

## 📱 Supported UPI Apps & Receipt Formats

Supports **direct shared receipts**, **full phone screenshots**, and **natural text messages** across all Indian payment and banking platforms:

| App / Platform | Supported Inputs | Extracted Entities |
| :--- | :--- | :--- |
| **Amazon Pay** | Direct Share Image, Screenshot, Chat Capture | Amount (`₹400`), Recipient (`Kanagarlasaiakhil`), Bank (`Statebankof India`), UPI Ref/UTR (`625827208126`), Date & Time |
| **Google Pay (GPay)** | Screenshot, Shared Receipt, Typed Text | "Paid to", "Payment to", UPI transaction ID, Debited Bank Account, Amount |
| **PhonePe** | Screenshot, Shared Receipt, Typed Text | "Paid Successfully", "Payment to", UTR, Bank Account, Amount |
| **Paytm** | Screenshot, Shared Receipt, Typed Text | "Money Received / Paid", Amount, Recipient/Sender, UPI Ref No, Bank |
| **CRED / CRED UPI** | Screenshot, Shared Receipt, Typed Text | "CRED UPI", "Cred Protected", UTR, Recipient, Amount |
| **BHIM UPI** | Screenshot, Shared Receipt, Typed Text | "Paid ₹4000.00", Banking Name, Transaction ID, Debited Bank |
| **YONO SBI / SBI** | Screenshot, Shared Receipt, Typed Text | YONO SBI transfers, State Bank of India, UTR, Amount |
| **Super.money** | Screenshot, Shared Receipt, Typed Text | Flipkart Super.money UPI receipts, UTR, Amount |
| **Navi / NaviPay** | Screenshot, Shared Receipt, Typed Text | Navi UPI receipts, UTR, Amount, Electricity / Merchant |
| **Union EASE / Vyom** | Screenshot, Shared Receipt, Typed Text | Union Bank transfers, Account, UTR |
| **WhatsApp Pay** | Screenshot, Shared Receipt, Typed Text | WhatsApp Pay transfers, `@waaxis`, UTR |

---

## ⚙️ How the Engine Works (Under the Hood)

When you upload a receipt screenshot in Telegram:
1. **Tier 1 (Gemini 3.6 Flash Vision AI):**
   - The bot sends the image directly to Google Gemini 3.6 Flash using structured JSON generation (`response_mime_type="application/json"`).
   - Gemini understands handwritten text, dark mode, stylized fonts, and complex bill layouts with ~100% accuracy in ~1 second.
2. **Tier 2 (RapidOCR + Heuristic Fallback):**
   - If offline or API key is absent, the bot seamlessly falls back to RapidOCR ONNX + regex heuristic parsers.
3. **Ledger & Balance Recalculation:**
   - The transaction is recorded into SQLite.
   - `balance_before` and `balance_after` are calculated in real-time.
   - Duplicate prevention checks if the 12-digit UTR was already recorded.
4. **Immediate Image Cleanup:**
   - The temporary image file is **deleted immediately** after data extraction so server disk space is never wasted.
   - All extracted financial data is preserved permanently in the database.
5. **Instant Cloud Backup:**
   - Database state is serialized and backed up to private Telegram storage.
6. **HTML Response Card:**
   - The user receives a clean, bold Telegram card with zero raw markdown symbols.

---

## 📋 Bot Commands & Features

| Command / Input | Example | Description |
| :--- | :--- | :--- |
| **Natural Text** | `Paid 500 to Ramesh`<br>`Received 6200 from Johnson` | Automatically detects amount, person, date, and logs Sent/Received |
| **Receipt Upload** | *(Send image screenshot)* | AI scans image, extracts amount, recipient, bank & UTR. Temporary image deleted immediately. |
| **Search by Amount** | `500` or `5000`<br>`/amount 5000` | Displays all transactions matching the exact amount |
| **`/balance`** | `/balance` | Current balance with structured All-Time and Today breakdown |
| **`/today`** | `/today` | Quick breakdown of today's total transactions and net balance |
| **`/history`** | `/history` | Clean, compact list of all transactions with consecutive IDs (`1..N`), amounts, dates & running balances |
| **`/details` / `/ids`** | `/details` | Shows database IDs, bank details, and full 12-digit UTR numbers |
| **`/date <date>`** | `/date yesterday`<br>`/date 15/09/2026` | Shows all transactions recorded on a specific date |
| **`/search <query>`** | `/search Balaji`<br>`/search Amazon Pay` | Searches transactions by person name, bank, or UTR reference |
| **`/filter`** | `/filter` | Interactive button menu (Today, Yesterday, This Month, Sent Only, Received Only) |
| **`/sort`** | `/sort` | Sorts transactions by amount (High ➔ Low / Low ➔ High) or date |
| **`/monthly`** | `/monthly` (or `/stats`) | Monthly financial analytics: total sent, received, net savings, and top recipient |
| **`/insights`** | `/insights` | AI-powered category breakdown, spending percentages (`🍔 Food`, `🛒 Groceries`, `🚕 Travel`), & smart advice |
| **`/budget`** | `/budget` | Monthly budget progress bar (`[██████░░░░] 60%`), remaining funds & health indicator |
| **`/setbudget <amt>`** | `/setbudget 20000` | Sets monthly spending limit with proactive threshold warnings (at 50%, 80%, 100%) |
| **`/digest`** | `/digest`<br>`/digest 2026-09-15` | Generates closing financial digest on demand (also automated at 9:00 PM IST daily) |
| **`/dashboard`** | `/dashboard` | Interactive Dark-Mode Web Dashboard with live Chart.js charts, donut categories & cash flow bars |
| **`/edit`** | `/edit`<br>`/edit 1` | Interactive menu to edit amount, name, date, type, or UTR with real-time balance recalculation |
| **`/delete`** | `/delete`<br>`/delete 1` | Deletes transaction, automatically resequences remaining IDs (no gaps), and recalculates balances |
| **`/setbalance <amt>`** | `/setbalance 50000` | Sets starting balance anchor and recalculates entire transaction history consistently |
| **`/export`** | `/export` | Generates official PDF Statement or Excel spreadsheet (`.xlsx`) |
| **`/help`** | `/help` | Complete interactive guide |

---

## 🎨 Clean HTML UI Output Example

```html
✅ <b>🔴 Payment Sent</b>

👤 <b>To:</b> Kanagarlasaiakhil
💵 <b>Amount:</b> <b>₹400.00</b> • <i>Amazon Pay</i>
📅 <b>Date:</b> 15 Sep 2026 (7:54 PM)
🏷️ <b>Category:</b> 👥 Transfers & P2P
🏦 <b>Bank:</b> Statebankof India
🔢 <b>Ref / UTR:</b> <code>625827208126</code>

💰 <b>Balance:</b> ₹7,400.00 ➔ <b>₹7,000.00</b>
```

---

## 🛠️ Core Engineering Engines

### 1. Gemini Vision AI Engine (`ocr/gemini_vision.py`)
- Multimodal parsing using **Gemini 3.6 Flash**.
- Extracts amount, transaction type, recipient/sender, payment app, bank name, UTR, and timestamp in structured JSON.

### 2. Smart Auto-Categorization & Insights (`services/category_service.py`)
- Automatically classifies payments into `Food & Dining`, `Groceries`, `Shopping`, `Travel & Transport`, `Bills & Utilities`, `Entertainment`, `Health & Medical`, and `Transfers & P2P`.
- Computes spending shares, category percentages, and financial health advice.

### 3. Proactive Budget Tracking (`services/budget_service.py`)
- Real-time monitoring against monthly limits.
- Generates 10-block visual progress bars and attaches threshold warnings (50%, 80%, 100%) directly to receipt confirmations.

### 4. Automated 9:00 PM IST Closing Digest (`services/scheduler_service.py`)
- Background timer delivers daily financial briefings in Indian Standard Time (IST) summarizing money in, money out, net flow, and closing balance.

### 5. Interactive Dark-Mode Web Analytics Dashboard (`web/templates/dashboard.html`)
- Built-in HTTP server on port `$PORT` serving `/dashboard` and `/api/data` JSON with Chart.js donut and bar graphs.

### 6. Automatic ID Re-Sequencing Engine (`services/balance_service.py`)
- Deleting any transaction (e.g. `#13`) automatically re-numbers remaining records sequentially (`1..N`) without gaps and resets the autoincrement sequence.

### 7. Real-Time Dynamic Balance Propagation Engine
- When any past transaction is edited or deleted, `recalculate_all_balances()` cascades balance updates across all subsequent transactions to guarantee exact ledger balance math.

### 8. Zero Data Loss Telegram Cloud Sync (`services/backup_service.py`)
- Pinned `#PAYMENT_TRACKER_BACKUP` in Telegram guarantees 100% data persistence across Render server restarts and redeployments.

---

## 🧪 Test Suite

All **47 unit tests** are automated and passing:
- `tests/test_gemini_vision.py` ✅
- `tests/test_category_service.py` ✅
- `tests/test_budget_service.py` ✅
- `tests/test_scheduler_service.py` ✅
- `tests/test_dashboard_api.py` ✅
- `tests/test_gdrive_service.py` ✅
- `tests/test_parsers.py` ✅
- `tests/test_resequence.py` ✅

