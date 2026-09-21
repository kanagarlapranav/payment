# Payment Tracker Telegram Bot — Project Summary & Architecture Guide
**Last Updated**: 21 Sep 2026

A 24/7 autonomous financial companion and personal ledger bot built on Telegram. Automatically extracts and records UPI payment receipts (via **Google Gemini Vision AI API** with multi-model fallback & **RapidOCR** backup), tracks real-time account balances, manages retroactive edits with dynamic recalculation, provides an intelligent **Pure Vegetarian Cafeteria Menu & Spending Tracker**, automated daily digests, proactive budget tracking, dark-mode web analytics dashboard, complete undo management, and exports professional PDF/Excel statements.

---

## 🚀 Live Production Status

- **Status**: Active & Live (Production)
- **Deployment URL**: `https://payment-3-kldp.onrender.com`
- **Repository**: `https://github.com/kanagarlapranav/payment.git` (Branch: `fix/ledger-backup-security-ux`)
- **Total Unit & Integration Tests**: 330+ passing tests (100% pass rate)
- **Runtime**: Python 3.13 / `python-telegram-bot` (v22+ AsyncIO with JobQueue)
- **Deployment Platform**: Render Web Service (24/7 Always-On Background Polling + HTTP Keep-Alive Self-Pinger)
- **Primary Vision AI**: Google Gemini Vision (`gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-flash-latest` with in-memory compression)
- **Backup OCR**: Local RapidOCR ONNX Runtime (no external Tesseract required)
- **Cloud Storage & Sync**: Triple-tier backup (Telegram Cloud Pinned Msg + Google Drive 5TB + Local JSON Sync with Backup Format v2 & SHA-256 Checksums)

---

## 📱 Supported UPI Apps & Receipt Formats

Supports **direct shared receipts**, **full phone screenshots**, and **natural text messages** across all Indian payment and banking platforms:

| App / Platform | Supported Inputs | Extracted Entities |
| :--- | :--- | :--- |
| **Super.money** | Direct Share Image, Screenshot, Chat Capture | Amount (`₹20`), Recipient (`VIKRAMAN NAIR K`), Bank (`Federal Bank`), UPI Ref/UTR (`662474885797`), Date & Time |
| **Amazon Pay** | Direct Share Image, Screenshot, Chat Capture | Amount (`₹400`), Recipient (`Kanagarlasaiakhil`), Bank (`Statebankof India`), UPI Ref/UTR (`625827208126`), Date & Time |
| **Google Pay (GPay)** | Screenshot, Shared Receipt, Typed Text | "Paid to", "Payment to", UPI transaction ID, Debited Bank Account, Amount |
| **PhonePe** | Screenshot, Shared Receipt, Typed Text | "Paid Successfully", "Payment to", UTR, Bank Account, Amount |
| **Paytm** | Screenshot, Shared Receipt, Typed Text | "Money Received / Paid", Amount, Recipient/Sender, UPI Ref No, Bank |
| **CRED / CRED UPI** | Screenshot, Shared Receipt, Typed Text | "CRED UPI", "Cred Protected", UTR, Recipient, Amount |
| **BHIM UPI** | Screenshot, Shared Receipt, Typed Text | "Paid ₹4000.00", Banking Name, Transaction ID, Debited Bank |
| **YONO SBI / SBI** | Screenshot, Shared Receipt, Typed Text | YONO SBI transfers, State Bank of India, UTR, Amount |
| **Navi / NaviPay** | Screenshot, Shared Receipt, Typed Text | Navi UPI receipts, UTR, Amount, Electricity / Merchant |
| **Union EASE / Vyom** | Screenshot, Shared Receipt, Typed Text | Union Bank transfers, Account, UTR |
| **WhatsApp Pay** | Screenshot, Shared Receipt, Typed Text | WhatsApp Pay transfers, `@waaxis`, UTR |

---

## 🍽️ Pure Vegetarian Cafeteria Management System

Customized specifically for your college cafeteria (**VIKRAMAN NAIR K**):

### 1. Auto-Detection & Interactive Selection
- Whenever a payment to **VIKRAMAN NAIR K** / `vikramannair066@fbl` is recorded, the bot automatically tags it as 🍔 **Food & Dining** and displays interactive item selection buttons:
  - **`1️⃣ 1 Item Mode`**: Shows all single items that match the bill amount exactly.
  - **`2️⃣ 2 Items Mode`**: Shows popular 2-item pairings summing to the bill (e.g., *Rice + Packing*, *Dosa + Tea*).
  - **`🛒 Build Plate / Cart`**: Multi-item builder allowing you to tap and bundle multiple dishes onto a single plate.
  - **`🍨 Ice Cream (Enter ₹)`**: Instant custom prompt to type the exact Ice Cream price (e.g. `40`, `60`).
  - **Add-on Buttons**: `📦 +₹5 Packing Charge`, `🌶️ +₹5 Extra Spicy`.

### 2. Menu Customization & Database Storage
- **100% Pure Vegetarian**: Strictly rejects any non-veg items.
- **Dynamic Database Table (`custom_menu_items`)**:
  - Add any custom dish or drink with price and category.
  - Automatically included in auto-suggestions, category browsing, and `/menu`.
- **Cafeteria Commands**:
  - `/menu` (or `/cafeteria`): Shows full categorized menu with `➕ Add Item`, `🗑️ Delete Item`, and `📊 Analytics`.
  - `/addmenu <Item Name> <Price> [Category]`: Adds new dishes (e.g. `/addmenu Mango Lassi 35 Beverages`).
  - `/delmenu [Item Name]`: Deletes custom dishes with interactive buttons or by name.
  - `/cafestats`: Monthly cafeteria spending analytics and top 5 most-ordered dishes.
  - `/cafeedit [ID]`: Re-tags or changes items on recent cafeteria payments.

---

## ⚙️ How the Vision & OCR Pipeline Works

1. **High-Speed In-Memory JPEG Compression:**
   - Resizes screenshots to 800x800 in memory (~70KB), cutting network payload and API response latency to under 3 seconds.
2. **Tier 1 (Google Gemini Vision AI with Multi-Model Fallback):**
   - Automatically queries `gemini-3.6-flash`, falling back to `gemini-3.7-flash` and `gemini-flash-latest` if rate-limited or unavailable.
   - Structured JSON response isolates payment card details from Telegram chat bubbles and captions.
3. **Tier 2 (RapidOCR + Chat Artifact Stripper):**
   - Embedded ONNX Runtime RapidOCR with chat artifact cleaning, extracting the actual receipt amount without external binaries.
4. **Immediate Ephemeral Cleanup:**
   - Temporary receipt images are deleted immediately after parsing to safeguard privacy and reduce disk footprint.
5. **Auto-Update & Duplicate Detection:**
   - Scanning a corrected or updated screenshot for a recent payment automatically updates the existing record with new amounts or recipients without creating duplicate entries.

---

## 📋 Complete Bot Command Reference

| Command / Input | Example | Description |
| :--- | :--- | :--- |
| **Receipt Upload** | *(Send image screenshot)* | AI scans image, extracts amount, recipient, bank & UTR. Temporary image deleted immediately. |
| **Natural Text** | `Paid 500 to Ramesh`<br>`Received 6200 from Johnson` | Instantly logs expense or income without forms or buttons |
| **`/balance`** | `/balance` | Current balance with structured All-Time and Today breakdown |
| **`/today`** | `/today` | Quick breakdown of today's total transactions and net balance |
| **`/history`** | `/history` | Interactive paginated ledger with filter chips (`All`, `Sent`, `Recv`) |
| **`/last5`** | `/last5` (or `/recent`) | Instantly displays the 5 most recent transactions without pagination |
| **`/details`** | `/details` | Shows database IDs, bank details, and full 12-digit UTR numbers |
| **`/date <date>`** | `/date yesterday`<br>`/date 15/09/2026` | Shows all transactions recorded on a specific date |
| **`/search <query>`** | `/search Vikraman`<br>`/search Super.money` | Searches transactions by person name, bank, or UTR reference |
| **`/amount <amt>`** | `/amount 20` | Finds transactions matching the exact amount |
| **`/filter`** | `/filter` | Interactive button menu (Today, Yesterday, This Month, Sent Only, Received Only) |
| **`/sort`** | `/sort` | Sorts transactions by amount or date |
| **`/monthly`** | `/monthly` (or `/stats`) | Monthly financial analytics: total sent, received, net savings, and top recipient |
| **`/insights`** | `/insights` | AI category breakdown, spending percentages, and smart advice |
| **`/budget`** | `/budget` | Monthly budget progress bar (`[██████░░░░] 60%`) and remaining funds |
| **`/setbudget <amt>`** | `/setbudget 20000` | Sets monthly spending limit with proactive threshold warnings (50%, 80%, 100%) |
| **`/digest`** | `/digest` | Generates closing financial digest on demand (also automated at `DAILY_DIGEST_TIME` IST) |
| **`/dashboard`** | `/dashboard` | Generates single-use 60-second secure login link for web analytics dashboard |
| **`/menu`** | `/menu` | Displays complete vegetarian cafeteria menu with add/delete buttons |
| **`/addmenu`** | `/addmenu Paneer Roll 45 Snacks` | Adds custom vegetarian item to menu database |
| **`/delmenu`** | `/delmenu Paneer Roll` | Removes custom item with interactive buttons or by name |
| **`/cafestats`** | `/cafestats` | Cafeteria monthly spending insights and top ordered dishes |
| **`/cafeedit`** | `/cafeedit` or `/cafeedit 4` | Opens item selector to update cafeteria transaction |
| **`/edit`** | `/edit` or `/edit 1` | Interactive menu to edit amount, name, date, type, or cafeteria order |
| **`/delete`** | `/delete` or `/delete 1` | Soft-deletes transaction (preserves immutable IDs & tombstones), recalculates balances & syncs cloud backup |
| **`/undo`** | `/undo` | Instantly reverses the last delete, edit, or add action |
| **`/setbalance <amt>`** | `/setbalance 50000` | Sets starting balance anchor and recalculates entire transaction history consistently |
| **`/restore`** | `/restore` | Idempotent upsert restore from cloud or clean JSON backup without wiping existing data |
| **`/export`** | `/export` (or `/report`, `/statement`) | Generates official PDF Statement or Excel spreadsheet (`.xlsx`) |
| **`/help`** | `/help` | Complete comprehensive interactive guide |
