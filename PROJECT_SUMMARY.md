# Payment Tracker Telegram Bot — Project Summary & Architecture Guide
**Last Updated & Verified**: 10 Oct 2026 (All 151 commits synced to GitHub & Render, Cloud Backup Rev 608)

A 24/7 autonomous financial companion and personal ledger bot built on Telegram. Automatically extracts and records UPI payment receipts (via **Google Gemini Vision AI API** with multi-model fallback & **RapidOCR** backup), tracks real-time account balances, manages retroactive edits with dynamic recalculation, provides an intelligent **Pure Vegetarian Cafeteria Menu & Spending Tracker**, automated daily digests, proactive budget tracking, dark-mode web analytics dashboard, complete undo management, and exports professional PDF/Excel statements.

---

## 🚀 Live Production Status

- **Status**: Active & Live (Production)
- **Deployment URL**: `https://payment-3-kldp.onrender.com`
- **Repository**: `https://github.com/kanagarlapranav/payment.git` (Branch: `main`)
- **Total Unit & Integration Tests**: 606+ passing tests (100% pass rate, 74 subtests)
- **Runtime**: Python 3.13 / `python-telegram-bot` (v22+ AsyncIO with JobQueue)
- **Deployment Platform**: Render Web Service (24/7 Always-On Background Polling + HTTP Keep-Alive Self-Pinger)
- **Primary Vision AI**: Google Gemini Vision (`gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-flash-latest` with in-memory compression)
- **Backup OCR**: Local RapidOCR ONNX Runtime (no external Tesseract required)
- **Cloud Storage & Sync**: Triple-tier backup (Telegram Cloud Pinned Msg + Google Drive 5TB + Local JSON Sync with Backup Format v2 & SHA-256 Checksums)
- **Persistent Data Directory**: `~/.payment_tracker/data` (external to repo, survives redeploys)

---

## 🏗️ Architecture Overview

### Multi-Tenant Workspace System

The bot operates on a **workspace-scoped multi-tenant architecture**:

| Component | Description |
| :--- | :--- |
| **Workspaces** | Each Telegram chat (group or DM) maps to an isolated workspace with its own ledger, settings, and members |
| **RBAC Roles** | `owner`, `admin`, `member`, `viewer` — enforced on every command via `bot/auth.py` decorators |
| **Workspace Routing** | `get_workspace_context(update)` resolves the active workspace from chat context or user's DM default |
| **Workspace Switcher** | `/switch` command lets the owner toggle between personal and group workspaces in DM |
| **Invite System** | `/invite_member` generates HMAC-signed, expiry-limited invite links with role + usage caps |
| **Access Requests** | Non-members can `/request_access`; owner approves/denies via inline buttons |

### Data Persistence & Safety

| Layer | Detail |
| :--- | :--- |
| **External DATA_DIR** | Default: `~/.payment_tracker/data` — outside the repo so redeploys never wipe live data |
| **Startup Guard** | Loud `⚠️ CRITICAL PERSISTENCE WARNING` logged if `DATA_DIR` resolves inside the repo |
| **Test Isolation** | `IS_TEST_ENV` enforces `DATA_DIR`, `DATABASE_PATH`, `LOG_DIR` must point to isolated temp dirs; refuses to touch production or repo-internal paths |
| **Format v2 Backups** | Workspace-scoped JSON exports with SHA-256 canonical checksums, monotonic revisions, and idempotent upsert restore |
| **Triple Backup** | Telegram Cloud (pinned) + Google Drive + Local JSON — automatic on every mutation |

### Database Schema (SQLite3, 5 migrations)

| Table | Purpose |
| :--- | :--- |
| `transactions` | Immutable ledger with soft-delete (`deleted_at`), balance snapshots (`balance_before`, `balance_after`), workspace scoping, and global UID uniqueness |
| `workspaces` | Workspace registry with `chat_id`, `chat_type`, `title`, `is_active`, `owner_id` |
| `workspace_members` | RBAC membership: `telegram_user_id`, `role`, `workspace_id` |
| `workspace_settings` | Per-workspace key-value settings (caps, quotas, roles, restricted users) |
| `settings` | Global settings (`default_workspace_id`, `balance`, `revision`) |
| `undo_log` | Reversible action journal for `/undo` support |
| `custom_menu_items` | Workspace-scoped cafeteria menu items |
| `budgets` | Monthly budget targets per workspace |
| `recurring_payments` | Scheduled recurring transaction templates |
| `monthly_reviews` | Monthly closing summary snapshots |
| `access_requests` | Pending workspace join requests |
| `audit_log` | Immutable security audit trail |

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
- **Dynamic Database Table (`custom_menu_items`)**: Workspace-scoped, add any custom dish or drink with price and category.
- **Cafeteria Commands**: `/menu`, `/addmenu`, `/delmenu`, `/cafestats`, `/cafeedit`

---

## ⚙️ How the Vision & OCR Pipeline Works

1. **High-Speed In-Memory JPEG Compression:** Resizes screenshots to 800x800 in memory (~70KB), cutting network payload and API response latency to under 3 seconds.
2. **Tier 1 (Google Gemini Vision AI with Multi-Model Fallback):** Automatically queries `gemini-3.6-flash`, falling back to `gemini-3.7-flash` and `gemini-flash-latest` if rate-limited or unavailable.
3. **Tier 2 (RapidOCR + Chat Artifact Stripper):** Embedded ONNX Runtime RapidOCR with chat artifact cleaning.
4. **Immediate Ephemeral Cleanup:** Temporary receipt images deleted immediately after parsing.
5. **Auto-Update & Duplicate Detection:** Scanning a corrected screenshot for a recent payment automatically updates the existing record.

---

## 📋 Complete Bot Command Reference

| Command / Input | Example | Description |
| :--- | :--- | :--- |
| **Receipt Upload** | *(Send image screenshot)* | AI scans image, extracts amount, recipient, bank & UTR |
| **Natural Text** | `Paid 500 to Ramesh` | Instantly logs expense or income |
| **`/balance`** | `/balance` | Current balance with All-Time and Today breakdown |
| **`/today`** | `/today` | Quick breakdown of today's transactions |
| **`/history`** | `/history` | Interactive paginated ledger with filter chips |
| **`/last5`** | `/last5` | 5 most recent transactions |
| **`/details`** | `/details` | Shows database IDs, bank details, and UTR numbers |
| **`/date <date>`** | `/date yesterday` | Transactions on a specific date |
| **`/search <query>`** | `/search Vikraman` | Search by person name, bank, or UTR |
| **`/amount <amt>`** | `/amount 20` | Find transactions matching exact amount |
| **`/filter`** | `/filter` | Interactive filter menu |
| **`/sort`** | `/sort` | Sort by amount or date |
| **`/monthly`** | `/monthly` | Monthly financial analytics |
| **`/insights`** | `/insights` | AI category breakdown and smart advice |
| **`/budget`** | `/budget` | Monthly budget progress bar |
| **`/setbudget <amt>`** | `/setbudget 20000` | Set monthly spending limit |
| **`/digest`** | `/digest` | Financial digest (also automated daily) |
| **`/dashboard`** | `/dashboard` | Secure single-use web analytics dashboard link |
| **`/menu`** | `/menu` | Vegetarian cafeteria menu |
| **`/addmenu`** | `/addmenu Paneer Roll 45 Snacks` | Add custom menu item |
| **`/delmenu`** | `/delmenu Paneer Roll` | Remove menu item |
| **`/cafestats`** | `/cafestats` | Cafeteria spending insights |
| **`/cafeedit`** | `/cafeedit 4` | Update cafeteria order |
| **`/edit`** | `/edit 1` | Edit amount, name, date, type |
| **`/delete`** | `/delete 1` | Soft-delete with tombstone preservation |
| **`/undo`** | `/undo` | Reverse last action |
| **`/setbalance <amt>`** | `/setbalance 50000` | Set starting balance anchor |
| **`/restore`** | `/restore` | Restore from cloud or JSON backup |
| **`/export`** | `/export` | PDF statement or Excel spreadsheet |
| **`/settings`** | `/settings` | Interactive workspace settings panel |
| **`/switch`** | `/switch` | Switch active workspace in DM |
| **`/permissions`** | `/permissions` | View RBAC role and capabilities |
| **`/setrole`** | `/setrole @user admin` | Set member role |
| **`/invite_member`** | `/invite_member member 5 48` | Generate invite link |
| **`/request_access`** | `/request_access` | Request workspace access |
| **`/help`** | `/help` | Complete interactive guide |

---

## 🛡️ Security & Audit

- **Immutable Audit Log**: Every mutation (add, edit, delete, setbalance, settings change) is recorded with actor, role, workspace, timestamp, and diff details.
- **Owner-Only Commands**: `restore`, `setbalance`, `delete`, `settings`, `setrole` require owner verification via `require_owner`.
- **HTML Injection Prevention**: All user-facing output sanitized via `utils/html_safety.py`.
- **Workspace Isolation**: Every query is scoped to `workspace_id`; cross-tenant reads are impossible.
- **Backup Integrity**: SHA-256 canonical checksums reject tampered or corrupted backup files.
- **Honest Restore Errors**: `/restore` distinguishes "no backup found" from "backup found but import failed" (e.g. UID constraint violations), showing the real error instead of misleading messages.

---

## 🔧 Recent Changes (Round 4–5)

### Round 5: Recovery & Hardening (Oct 2026)

**Root Cause**: A redeploy wiped the in-repo `data/` directory. The bot auto-restored from a Telegram-pinned backup, importing rows with OLD workspace IDs into a fresh DB with NEW workspace IDs. All 37 rows became invisible due to workspace-scoped fail-closed reads.

| Work Item | What Changed |
| :--- | :--- |
| **1. Stranded Row Repair** | Extended `scripts/repair_workspace_provenance.py` with 3-pass fallback matching: `chat_match` → `user_match` → `owner_default` → `skipped:<reason>`. Applied to both `transactions` and `undo_log`. Balance recalculation on touched workspaces. All 38 rows recovered (36 group + 2 personal). |
| **2. External DATA_DIR** | Moved default `DATA_DIR` from `<repo>/data` to `~/.payment_tracker/data`. Loud startup warning if `DATA_DIR` is inside `BASE_DIR`. Test guards block both production and repo-internal paths. Live DB migrated to external location. |
| **3. Honest Restore Errors** | Introduced `TelegramRestoreResult(success, found, error)` with `__bool__`/`__eq__` backward compat. `restore_command` now shows `❌ Cloud Restore Failed: <real error>` instead of misleading "No backup file found". |

### Round 4: Workspace Settings & Scoping (Oct 2026)

| Feature | Detail |
| :--- | :--- |
| **Interactive Settings Panel** | `/settings` command with inline keyboard sub-panels for per-transaction cap, monthly cap, default member role, restricted users, Gemini quota, and quick-add confirmation |
| **DB-Backed Settings** | `workspace_settings` table with per-workspace key-value pairs; `config.py` helper functions (`get_per_transaction_cap`, `get_monthly_spending_cap`, `get_default_member_role`, etc.) query DB with env-var fallback |
| **Workspace Status & Migration v5** | `workspace_status` and `member_status` columns; `utils/migrate_v5.py` handles schema upgrades |

### Earlier Rounds (Sep–Oct 2026)

- **Multi-Tenant Workspace Architecture** (Round 1–2): Workspace isolation, RBAC, routing, switcher UX.
- **Workspace Collision Fixes** (Round 3): Group/DM workspace deduplication, provenance tagging, member auto-provisioning.
- **Backup Format v2**: Workspace-scoped exports with checksums, monotonic revisions, idempotent upsert.
- **Web Dashboard**: Dark-mode analytics with secure single-use token auth.
- **Soft-Delete & Undo**: Immutable tombstones with full undo journal.
- **Recurring Payments**: Scheduled templates with auto-execution via JobQueue.

---

## 📁 Project Structure

```
payment_tracker/
├── app.py                    # Application entry point, command/handler registration
├── config.py                 # Environment config, DATA_DIR, DB_PATH, workspace settings helpers
├── bot/
│   ├── auth.py               # RBAC decorators (require_owner, require_admin, require_member)
│   ├── commands.py           # All /command handlers + settings panel
│   ├── handlers.py           # Message handlers (image, text parsing)
│   ├── keyboards.py          # Inline keyboard builders
│   └── callbacks/            # Callback query handlers (cafe, nav)
├── database/
│   ├── db.py                 # Schema setup, migrations v1–v5, connection management
│   ├── queries.py            # All DB queries (workspace-scoped)
│   └── models.py             # Data models
├── services/
│   ├── backup_service.py     # Export/import, Format v2, TelegramRestoreResult, cloud sync
│   ├── balance_service.py    # Balance recalculation engine
│   ├── transaction_service.py # Transaction CRUD with audit logging
│   ├── undo_service.py       # Undo journal management
│   ├── audit_service.py      # Immutable audit log
│   ├── scheduler_service.py  # JobQueue scheduling (digests, recurring)
│   ├── invite_service.py     # HMAC invite link generation/validation
│   ├── dashboard_auth.py     # Single-use token auth for web dashboard
│   └── ...                   # budget, cafeteria, category, duplicate, export, gdrive, etc.
├── ocr/
│   ├── gemini_vision.py      # Gemini Vision AI with multi-model fallback
│   ├── engine.py             # RapidOCR ONNX engine
│   └── extractor.py          # Receipt field extraction
├── parsers/                  # UPI app-specific parsers (amazonpay, googlepay, phonepe, etc.)
├── utils/
│   ├── validation.py         # Input validation, UID generation
│   ├── html_safety.py        # HTML injection prevention
│   ├── dates.py              # Date parsing and timezone handling
│   ├── migrate_v5.py         # Schema migration v5
│   └── ...                   # currency, cleanup, hashing, workspace_storage
├── scripts/
│   └── repair_workspace_provenance.py  # One-time stranded row repair with 3-pass fallback
├── tools/                    # CLI utilities (check_ledger, verify_backup, verify_test_isolation)
├── web/templates/            # Dashboard HTML template
├── tests/                    # 606+ tests (unit, integration, RBAC, backup, security, etc.)
└── data/                     # Repo-internal data (gitignored); live data at ~/.payment_tracker/data
```

---

## 🧪 Test Coverage

- **606 tests passing** (100% pass rate) across 40+ test files
- Key test suites: workspace RBAC routing, collision fixes, backup/restore integrity, multi-tenant hardening, user isolation, settings scoping, provenance repair, soft-delete/undo, dashboard security, ledger reliability, HTML safety, scheduler, and end-to-end integration scenarios.
