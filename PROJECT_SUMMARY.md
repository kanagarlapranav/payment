# 💰 Payment Tracker Bot - Complete Reference & Documentation

## 🔗 Live Links & Project Reference
- **GitHub Repository**: [https://github.com/kanagarlapranav/payment](https://github.com/kanagarlapranav/payment)
- **Render Live URL**: [https://payment-3-kldp.onrender.com](https://payment-3-kldp.onrender.com/)
- **Render Dashboard**: [https://dashboard.render.com](https://dashboard.render.com/)
- **Deployment Type**: Render Web Service (Free Tier, 24/7 Always-On Background Polling + HTTP Health Server)

---

## 📱 Supported UPI Apps & Receipt Formats

The bot supports **screenshots**, **shared receipts (Image + Text caption)**, and **typed messages** for all major Indian UPI and banking apps:

| App | Supported Input Types | Key Features Extracted |
| :--- | :--- | :--- |
| **BHIM UPI** | Screenshot, Shared Receipt, Typed Text | "Paid ?4000.00", Banking Name, Transaction ID, Debited Bank/Account, Sender/Recipient |
| **Paytm** | Screenshot, Shared Receipt, Typed Text | "Money Received", Amount in Words, Sender/Recipient, UPI Ref No, Union Bank/SBI/HDFC |
| **PhonePe** | Screenshot, Shared Receipt, Typed Text | "Paid Successfully", "Payment to", UTR, Bank Account |
| **Google Pay (GPay)** | Screenshot, Shared Receipt, Typed Text | "Paid to", "Payment to", UPI transaction ID, Bank |
| **CRED / CRED UPI** | Screenshot, Shared Receipt, Typed Text | "CRED UPI", "Cred Protected", UTR, Recipient |
| **Super.money (Supermoney)** | Screenshot, Shared Receipt, Typed Text | Flipkart Super.money UPI receipts, UTR, Amount |
| **Navi / NaviPay** | Screenshot, Shared Receipt, Typed Text | Navi UPI receipts, UTR, Amount, Electricity / Merchant / P2P |
| **YONO SBI** | Screenshot, Shared Receipt, Typed Text | YONO SBI transfers, State Bank of India, UTR |
| **Union EASE / Vyom** | Screenshot, Shared Receipt, Typed Text | Union EASE / Vyom Union Bank transfers, Account, UTR |

---

## 🤖 Bot Features & Complete Command Guide

Both forward slashes (`/command`), backslashes (`\command`), and plain natural text are fully supported:

| Command / Input | Example | Description |
| :--- | :--- | :--- |
| **Natural Text** | `Paid 500 to Ramesh`<br>`Received 2000 from Balaji` | Automatically detects amount, person, date, and logs Sent/Received |
| **Receipt Upload (OCR)** | *(Upload BHIM, GPay, PhonePe, Paytm, CRED, Super.money, etc.)* | Scans image text, extracts amount, recipient/sender, UTR/Ref No. Deletes image immediately to save disk. |
| **Search by Amount** | `500` or `₹5000`<br>`/amount 5000` | Displays all transactions matching the exact amount with total sum |
| **`/balance`** | `\balance` | Current wallet balance & today's spending/income summary |
| **`/today`** | `\today` | Quick breakdown of today's total transactions and net balance |
| **`/history`** | `\history` | Clean list of recent transactions (dates formatted as `06 Sep 2026`, color-coded `🟢/🔴` badges) |
| **`/sort`** | `/sort high`<br>`/sort low`<br>`/sort date` | Sorts transactions by amount (High ➔ Low / Low ➔ High) or date with interactive buttons |
| **`/monthly`** | `/monthly`<br>`/stats` | Monthly financial analytics: total sent, received, net savings, and top recipient |
| **`/date <date>`** | `/date yesterday`<br>`/date 06/09/2026`<br>`yesterday` | Shows all transactions on a specific date |
| **`/search <query>`** | `/search Balaji`<br>`/search Paytm` | Searches transactions by person name, note, bank, or UTR |
| **`/filter`** | `\filter` | Interactive button menu (Today, Yesterday, This Month, Sent Only, Received Only) |
| **`/edit`** | `\edit`<br>`/edit 1` | Interactive menu to edit amount, name, date, or reference with synchronized cloud backup |
| **`/delete`** | `\delete`<br>`/delete 1` | Interactive 1-tap confirmation menu to delete transactions with synchronized cloud backup |
| **`/details` / `/ids`** | `/ids` | Shows technical transaction IDs and full UTR reference numbers |
| **`/setbalance <amt>`** | `/setbalance 50000` | Sets starting balance and anchors recalculation baseline |
| **`/export`** | `\export` | Generates official PDF Statement or Excel spreadsheet (`.xlsx`) |
| **`/help`** | `\help` | Displays the complete help guide |

---

## ☁️ Architecture & Resilience

```
[ Telegram App ]  📱
       ↕ (Messages & Receipts)
[ Render Cloud Server ]  ☁️  (Runs 24/7 on Free Tier)
       ↕
 ├── 🤖 OCR Engine (RapidOCR ONNX + Tesseract) ➔ Fast non-blocking OCR
 ├── 🧠 Multi-App UPI Parser Engine           ➔ Extracts all transaction attributes
 ├── 💾 SQLite Database                        ➔ Local transaction & balance tracking
 └── ☁️ Telegram Cloud Auto-Backup             ➔ Pinned JSON backup keeps data 100% persistent
```

1. **Auto-Backup & Auto-Restore**: Every transaction add, edit, delete, and balance adjustment triggers an automatic cloud backup (`#PAYMENT_TRACKER_BACKUP`) pinned in Telegram. When Render spins up or redeploys, it automatically restores the exact latest state.
2. **Zero Disk Bloat**: All temporary receipt images are deleted immediately after OCR extraction.
3. **24/7 Keep-Alive**: Background keep-alive server and self-pinger prevent container sleeping.
