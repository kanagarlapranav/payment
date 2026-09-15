# 💰 Payment Tracker Bot - Complete Reference & Documentation

> **Last Updated:** 15 September 2026  
> **Status:** Live & Production Ready (Render Always-On Service)

---

## 🔗 Live Links & Project Reference
- **GitHub Repository**: [https://github.com/kanagarlapranav/payment](https://github.com/kanagarlapranav/payment)
- **Render Live URL**: [https://payment-3-kldp.onrender.com](https://payment-3-kldp.onrender.com/)
- **Render Dashboard**: [https://dashboard.render.com](https://dashboard.render.com/)
- **Deployment Type**: Render Web Service (Free Tier, 24/7 Always-On Background Polling + HTTP Health Server)

---

## 📱 Supported UPI Apps & Receipt Formats

The bot supports **direct shared receipts**, **full phone screenshots (including Telegram chat captures)**, and **natural text messages** for all major Indian UPI and banking platforms:

| App | Supported Input Types | Key Features Extracted |
| :--- | :--- | :--- |
| **Amazon Pay** | Direct Share Image, Screenshot, Chat Capture | Receipt isolation, OCR artifact handling (`OPaidsuccessfully`, `Biank`, `I0`, `Arnizon`), Amount (`₹400`), Recipient (`Kanagarla Sai Akhil`), Bank (`State Bank of India ****7751`), UPI Ref/UTR (`625827208126`) |
| **BHIM UPI** | Screenshot, Shared Receipt, Typed Text | "Paid ?4000.00", Banking Name, Transaction ID, Debited Bank/Account, Sender/Recipient |
| **Paytm** | Screenshot, Shared Receipt, Typed Text | "Money Received", Amount in Words, Sender/Recipient, UPI Ref No, Union Bank/SBI/HDFC |
| **PhonePe** | Screenshot, Shared Receipt, Typed Text | "Paid Successfully", "Payment to", UTR, Bank Account |
| **Google Pay (GPay)** | Screenshot, Shared Receipt, Typed Text | "Paid to", "Payment to", UPI transaction ID, Bank |
| **CRED / CRED UPI** | Screenshot, Shared Receipt, Typed Text | "CRED UPI", "Cred Protected", UTR, Recipient |
| **Super.money (Supermoney)** | Screenshot, Shared Receipt, Typed Text | Flipkart Super.money UPI receipts, UTR, Amount |
| **Navi / NaviPay** | Screenshot, Shared Receipt, Typed Text | Navi UPI receipts, UTR, Amount, Electricity / Merchant / P2P |
| **YONO SBI** | Screenshot, Shared Receipt, Typed Text | YONO SBI transfers, State Bank of India, UTR |
| **Union EASE / Vyom** | Screenshot, Shared Receipt, Typed Text | Union EASE / Vyom Union Bank transfers, Account, UTR |
| **WhatsApp Pay** | Screenshot, Shared Receipt, Typed Text | WhatsApp Pay transfers, `@waaxis`, UTR |
| **Mobikwik / Slice / Jupiter** | Screenshot, Shared Receipt, Typed Text | Specialized UPI handles and custom receipt layouts |

---

## 🤖 Bot Features & Complete Command Guide

Both forward slashes (`/command`), backslashes (`\command`), and plain natural text are fully supported:

| Command / Input | Example | Description |
| :--- | :--- | :--- |
| **Natural Text** | `Paid 500 to Ramesh`<br>`Received 2000 from Balaji` | Automatically detects amount, person, date, and logs Sent/Received |
| **Receipt Upload (OCR)** | *(Upload Amazon Pay, BHIM, GPay, PhonePe, Paytm, CRED, etc.)* | Scans image, extracts amount, recipient/sender, bank & 12-digit UTR. Deletes image immediately to save disk. |
| **Search by Amount** | `500` or `₹5000`<br>`/amount 5000` | Displays all transactions matching the exact amount with total sum |
| **`/balance`** | `\balance` | Current balance with structured All-Time and Today breakdown |
| **`/today`** | `\today` | Quick breakdown of today's total transactions and net balance |
| **`/history`** | `\history` | Clean, compact list of all transactions with consecutive IDs (`1..N`), amounts, dates & running balances |
| **`/sort`** | `/sort high`<br>`/sort low`<br>`/sort date` | Sorts transactions by amount (High ➔ Low / Low ➔ High) or date with interactive buttons |
| **`/monthly`** | `/monthly`<br>`/stats` | Monthly financial analytics: total sent, received, net savings, and top recipient |
| **`/date <date>`** | `/date yesterday`<br>`/date 15/09/2026`<br>`yesterday` | Shows all transactions recorded on a specific date |
| **`/search <query>`** | `/search Balaji`<br>`/search Paytm` | Searches transactions by person name, bank, or UTR reference |
| **`/filter`** | `\filter` | Interactive button menu (Today, Yesterday, This Month, Sent Only, Received Only) |
| **`/edit`** | `\edit`<br>`/edit 1` | Interactive menu to edit amount, name, date, type, or UTR with dynamic balance recalculation |
| **`/delete`** | `\delete`<br>`/delete 1` | Deletes transaction, automatically resequences remaining IDs (no gaps), and recalculates balances |
| **`/details` / `/ids`** | `/ids` | Shows technical transaction IDs, bank details, and full UTR numbers |
| **`/setbalance <amt>`** | `/setbalance 50000` | Sets starting balance anchor and recalculates entire transaction history consistently |
| **`/export`** | `\export` | Generates official PDF Statement or Excel spreadsheet (`.xlsx`) |
| **`/help`** | `\help` | Displays the complete help guide |

---

## 🎨 Clean UI & Message Design

### 1. 📸 Payment Recorded Confirmation
```markdown
✅ Payment Sent

👤 To: Balaji
💵 Amount: ₹5,000 • Paytm
📅 Date: 05 Sep 2026 (10:02 AM)
🏦 Bank: Union Bank Of India
🔢 Ref / UTR: 661385614715

💰 Balance: ₹36,900 ➔ ₹31,900
```

### 2. 📜 Payment History (`/history`)
```markdown
📜 Payment History

*1.* 🟢 ₹30,700 — Lakkimsetti Sai Sri Vamsi
   📅 04 Sep | 💰 Bal: ₹30,700

*2.* 🟢 ₹6,200 — Johnson Siddhu Motru
   📅 04 Sep | 💰 Bal: ₹36,900

*3.* 🔴 ₹5,000 — Balaji Icic Admin
   📅 05 Sep | 💰 Bal: ₹31,900

*4.* 🟢 ₹600 — Patchigolla Lakshmi Vinay
   📅 06 Sep | 💰 Bal: ₹32,500

━━━━━━━━━━━━━━━━━━━━
🟢 Total Received: ₹37,500
🔴 Total Sent: ₹5,000
💳 Current Balance: ₹32,500
```

### 3. ✏️ Transaction Edit Confirmation
```markdown
✅ Transaction #3 Updated

👤 Person: Balaji
💵 Amount: ₹5,000
💰 Balance Flow: ₹36,900 ➔ ₹31,900

💳 Current Balance: ₹31,900
```

### 4. 🗑️ Transaction Deletion with Auto-Resequencing
```markdown
🗑️ Transaction #13 Deleted

✅ Remaining transactions renumbered sequentially.
💰 Updated Balance: ₹32,500
```

---

## ⚙️ Core Technical Engines

### 1. Automatic Contiguous ID Re-Sequencing Engine
- **Problem Solved:** Deleting a transaction in standard SQL leaves holes (e.g. deleting `#13` leaves `#12` followed by `#14`).
- **Engine Logic:** `resequence_transaction_ids()` assigns consecutive integers `1, 2, 3... N` chronologically to all records and resets SQLite's autoincrement counter (`sqlite_sequence`) to `N`. New payments automatically receive ID `N + 1`.

### 2. Real-Time Dynamic Balance Propagation Engine
- **Engine Logic:** `recalculate_all_balances()` recalculates every transaction's `balance_before` and `balance_after` in sequence from the initial baseline anchor whenever amounts, types, or records are added, edited, or deleted.

### 3. Robust Dual-Engine OCR Pipeline
- **Engine:** RapidOCR (neural ONNX) with automatic CPU pre-warming and Tesseract fallback.
- **Memory Optimization:** Downscales oversized receipts, uses non-blocking async threads, and immediately deletes processed files to prevent Render memory/disk limits.

---

## ☁️ Architecture & Reliability

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

1. **Auto-Backup & Auto-Restore**: Every transaction add, edit, delete, and balance adjustment triggers an automatic cloud backup (`#PAYMENT_TRACKER_BACKUP`) pinned in Telegram. When Render spins up or redeploys, it automatically restores the latest database snapshot.
2. **Zero Disk Bloat**: All temporary receipt images are deleted immediately after OCR extraction.
3. **24/7 Keep-Alive**: Background keep-alive server and self-pinger prevent free-tier container sleeping.
