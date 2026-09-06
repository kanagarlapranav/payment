# 💰 Payment Tracker Bot - Complete Reference & Documentation

## 🔗 Live Links & Project Reference
- **GitHub Repository**: [https://github.com/kanagarlapranav/payment](https://github.com/kanagarlapranav/payment)
- **Render Live URL**: [https://payment-3-kldp.onrender.com](https://payment-3-kldp.onrender.com/)
- **Render Dashboard**: [https://dashboard.render.com](https://dashboard.render.com/)
- **Deployment Type**: Render Web Service (Free Tier, 24/7 Always-On Background Polling + HTTP Health Server)

---

## 🤖 Bot Features & Complete Command Guide

Both forward slashes (`/command`) and backslashes (`\command`) or plain text are fully supported:

| Command / Input | Example | Description |
| :--- | :--- | :--- |
| **Natural Text** | `Paid 500 to Ramesh`<br>`Received 2000 from Balaji` | Automatically detects amount, person, date, and logs Sent/Received |
| **Receipt Upload (OCR)** | *(Upload GPay, PhonePe, Paytm, CRED screenshot)* | Scans image text, extracts amount, recipient/sender, UTR/Ref No. |
| **Search by Amount** | `500` or `₹5000`<br>`/amount 5000` | Displays all transactions matching the exact amount with total sum |
| **`/balance`** | `\balance` | Current wallet balance & today's spending/income summary |
| **`/today`** | `\today` | Quick breakdown of today's total transactions and net balance |
| **`/history`** | `\history` | Clean list of recent transactions (dates formatted as `06 Sep 2026`, color-coded `🟢/🔴` badges) |
| **`/sort`** | `/sort high`<br>`/sort low`<br>`/sort date` | Sorts transactions by amount (High ➔ Low / Low ➔ High) or date with interactive buttons |
| **`/monthly`** | `/monthly`<br>`/stats` | Monthly financial analytics: total sent, received, net savings, and top recipient |
| **`/date <date>`** | `/date yesterday`<br>`/date 06/09/2026`<br>`yesterday` | Shows all transactions on a specific date |
| **`/search <query>`** | `/search Balaji`<br>`/search Paytm` | Searches transactions by person name, note, bank, or UTR |
| **`/filter`** | `\filter` | Interactive button menu (Today, Yesterday, This Month, Sent Only, Received Only) |
| **`/edit`** | `\edit` | Interactive 1-tap menu to edit amount, name, date, or reference |
| **`/delete`** | `\delete` | Interactive 1-tap confirmation menu to delete transactions |
| **`/details` / `/ids`** | `/ids` | Shows technical transaction IDs and full UTR reference numbers |
| **`/setbalance <amt>`** | `/setbalance 50000` | Manually sets or resets your starting balance |
| **`/export`** | `\export` | Generates and sends a downloadable Excel spreadsheet (`.xlsx`) |
| **`/help`** | `\help` | Displays the complete help guide |

---

## ☁️ Architecture & 24/7 Cloud Operation

```
[ Telegram App ]  📱
       ↕ (Messages & Receipts)
[ Render Cloud Server ]  ☁️  (Runs 24/7 on Free Tier)
       ↕
 ├── 🤖 AI / OCR Engine    ➔ Scans receipts (RapidOCR / Tesseract)
 ├── 🧠 Message Parser     ➔ Extracts amount, person, date, and type
 └── 💾 SQLite Database    ➔ Stores transactions & calculates live balance
```

1. **Render Free Web Service (`app.py`)**: Runs 24/7 without needing your PC. Runs a lightweight background HTTP port listener on `PORT 10000` for Render alongside Telegram long-polling.
2. **Persistent Storage**: All transaction records and balances are stored in `data/database.sqlite3`.
3. **Auto-Deployment**: Any commit pushed to GitHub's `main` branch automatically triggers Render to build and restart within seconds.

---

## 💡 How to Resume / Access this Chat in Antigravity

1. **Automatic Local Persistence**: Antigravity automatically persists all conversation turns, artifacts, and logs locally in your workspace.
2. **Left Sidebar History**: Click the history/session icon in the Antigravity left sidebar to open and continue this chat anytime.
3. **`/learn` command**: You can type `/learn` in chat to instruct Antigravity to memorize this project's full architecture across all future sessions.
