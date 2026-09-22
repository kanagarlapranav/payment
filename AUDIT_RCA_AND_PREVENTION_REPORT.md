# 🔒 AUDIT: Root Cause Analysis & Prevention Report

**Project**: Personal Finance Telegram Bot
**Report Date**: 21 September 2026
**Prepared For**: Auditor / Verification
**Status**: ✅ ALL ISSUES RESOLVED — SAFEGUARDS DEPLOYED

---

## 1. Executive Summary

A series of cascading failures caused **test data leakage** into the production database, resulting in:

- 🔴 **Fake transactions** (e.g., synthetic test transactions) appearing in `/history` output.
- 🔴 **Balance mismatches** on the dashboard.
- 🔴 **Checksum verification failures** on backup files.
- 🔴 **`UnboundLocalError`** crash on `/today` command.

**All issues have been resolved.** The genuine transaction ledger has been verified intact, and permanent architectural safeguards have been deployed to prevent recurrence.

| Metric | Before Fix | After Fix |
|--------|-----------|-----------|
| Production transactions | Genuine + synthetic | Genuine only |
| Balance | Polluted | Verified correct |
| Test isolation | ❌ None | ✅ Full tmpdir isolation |
| Checksum verification | ❌ Failing | ✅ Passing (legacy compat) |
| `/today` command | ❌ Crashing | ✅ Working |

---

## 2. Incident Timeline

| Time | Event |
|------|-------|
| 19 Sep 2026 | Developer runs `pytest` during feature development. Tests write directly into production database path. |
| 19 Sep 2026 | Test fixtures call `export_database_to_json()` which overwrites production backup JSON with test data. |
| 19 Sep 2026 | The corrupted backup is auto-pinned to Telegram cloud, overwriting the genuine backup. |
| 20 Sep 2026 | Render container restarts, fetches the test-polluted backup from Telegram, restoring fake data. |
| 20 Sep 2026 | User reports `/history` showing fake entries and balance mismatch. |
| 20 Sep 2026 | User reports `/today` crashing with `UnboundLocalError`. |
| 21 Sep 2026 | Root cause identified: no test isolation + redundant import causing scoping bug. |
| 21 Sep 2026 | **FIX**: Synthetic rows removed from SQLite. Genuine ledger restored. |
| 21 Sep 2026 | **FIX**: `conftest.py` created with full tmpdir isolation for all tests. |
| 21 Sep 2026 | **FIX**: `config.py` updated with automatic test environment detection. |
| 21 Sep 2026 | **FIX**: Hazardous `setUpClass`/`tearDownClass` byte-overwrite patterns removed from test files. |
| 21 Sep 2026 | **FIX**: Checksum backward compatibility added for legacy v2 format. |
| 21 Sep 2026 | **FIX**: Redundant `format_currency` import removed from `bot/handlers.py`. |
| 21 Sep 2026 | **VERIFIED**: Clean backup pinned to Telegram. Hash verification passed. |
| 21 Sep 2026 | **VERIFIED**: `tools/verify_test_isolation.py` confirms tests leave production data untouched. |

---

## 3. Root Cause Analysis

### 3.1 Test-Data Leakage into Production Database

**Root Cause**: `config.py` hardcoded `DATA_DIR = BASE_DIR / 'data'` with no test-environment awareness. When `pytest` executed, every test operated directly on the production `data/database.sqlite3` file.

**Affected File**: [`config.py`](file:///config.py)

**Before (vulnerable)**:
```python
DATA_DIR = BASE_DIR / 'data'  # Always points to production
```

**After (safe)**:
```python
is_test_env = (
    os.getenv('PAYMENT_TRACKER_ENV') == 'test'
    or 'PYTEST_CURRENT_TEST' in os.environ
    or 'pytest' in sys.modules
)
if is_test_env:
    DATA_DIR = Path(tempfile.gettempdir()) / 'payment_tracker_test_data'
else:
    DATA_DIR = BASE_DIR / 'data'
```

**Compounding Factor**: Test files used a hazardous `setUpClass`/`tearDownClass` pattern that byte-snapshotted the database before tests and restored it after. If any prior test had already dirtied the database, the snapshot captured the dirty state and `tearDownClass` wrote it back to disk.

---

### 3.2 Render Dashboard Showing Test Data

**Root Cause**: Render's free tier uses ephemeral containers. On each restart, `on_startup()` fetches the latest pinned backup from Telegram. Because the test run had exported a corrupt backup and pinned it to Telegram cloud, the Render container restored fake/test data on boot.

**Fix Applied**: The genuine backup was re-exported, re-pinned to Telegram, and the corrupted earlier pins were superseded.

---

### 3.3 Checksum Verification Mismatch

**Root Cause**: During development, `payload_for_hash` computation changed:
1. `budgets` field was added to the hash payload.
2. JSON serialization separators changed from `(', ', ': ')` (standard) to `(',', ':')` (compact).

**Affected File**: [`services/backup_service.py`](file:///services/backup_service.py)

**Fix**: Added dual-format backward compatibility in `verify_backup_payload()`:
```python
for seps in [(',', ':'), (', ', ': ')]:
    candidate_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=seps).encode()
    ).hexdigest()
    if candidate_hash == stored_checksum:
        return True
```

---

### 3.4 `UnboundLocalError` on `/today` Command

**Root Cause**: In [`bot/handlers.py`](file:///bot/handlers.py), a redundant import statement existed inside `handle_callback_query()`:

```python
from utils.formatting import format_currency
```

Python scoping rules treated `format_currency` as a local variable for the entire function scope once this import statement existed. Any earlier reference to `format_currency` crashed with `UnboundLocalError`.

**Fix**: Removed the redundant local import.

---

## 4. Anonymized Ledger Verification

The production database is verified intact with anonymized ledger metadata:

| Record ID | Type | Party | Status |
|-----------|------|-------|--------|
| REC_001 | RECEIVED | Counterparty_A | Active |
| REC_002 | RECEIVED | Counterparty_B | Active |
| REC_003 | SENT | Counterparty_C | Active |
| REC_004 | RECEIVED | Counterparty_D | Active |
| REC_005 | SENT | Counterparty_E | Active |

**Database File**: `data/database.sqlite3` — Verified 0 synthetic test rows.
**Backup File**: `data/backup_transactions.json` — Format v2, checksum verified.

---

## 5. Architectural Safeguards Implemented

### 5.1 Automatic Test Environment Detection ([`config.py`](file:///config.py))

Three independent signals detect a test environment:
1. `PAYMENT_TRACKER_ENV=test` environment variable
2. `PYTEST_CURRENT_TEST` environment variable
3. `'pytest' in sys.modules` runtime check

When ANY of these is true, `DATA_DIR` is automatically redirected to an isolated temporary directory.

### 5.2 Global Test Isolation Fixture ([`tests/conftest.py`](file:///tests/conftest.py))

- Creates a dedicated temporary directory (`tempfile.mkdtemp`) for each test session.
- Overrides all critical paths before any test module is imported.
- Monkeypatches path references per-test.

### 5.3 Removal of Hazardous `setUpClass` Byte Overwrites

Removed raw byte-copying setup/teardown hooks from all test files.

### 5.4 Verification Tool ([`tools/verify_test_isolation.py`](file:///tools/verify_test_isolation.py))

A standalone CLI tool that cryptographically verifies production database files remain byte-for-byte untouched during test runs.

---

## 6. Verification Commands

Run these commands to independently verify system integrity:

```bash
python tools/verify_test_isolation.py
python tools/check_ledger.py --database data/database.sqlite3
python tools/verify_backup.py data/backup_transactions.json
```

---

## 7. Conclusion

The three-layer defense (config auto-detection → conftest fixture → monkeypatch per-test) guarantees that no test execution can ever read, write, or pollute production data files.
