#!/usr/bin/env bash
# ==============================================================================
# Payment Tracker - Interactive .env Configuration Script
# Location on VM: /home/azureuser/payment/scripts/configure_env.sh
# Safely configures required production settings in .env without overwriting
# or deleting existing lines or leaking secrets to console output.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${1:-${REPO_DIR}/.env}"

KNOWN_USER_ID="8379948573"
KNOWN_GROUP_ID="-1004310685141"
DEFAULT_GEMINI_MODEL="gemini-3.6-flash"

echo "======================================================================"
echo " Payment Tracker - Environment Configuration Setup"
echo " Target .env file: ${ENV_FILE}"
echo "======================================================================"

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "${REPO_DIR}/.env.example" ]; then
        echo "==> .env not found. Initializing from .env.example..."
        cp "${REPO_DIR}/.env.example" "$ENV_FILE"
        chmod 600 "$ENV_FILE"
    else
        echo "==> Creating new empty .env file..."
        touch "$ENV_FILE"
        chmod 600 "$ENV_FILE"
    fi
fi

# Helper function to get current value from .env
get_env_val() {
    local key="$1"
    python3 -c "
import sys
path, k = sys.argv[1], sys.argv[2]
try:
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith(k + '='):
                print(line.split('=', 1)[1].strip())
                sys.exit(0)
except Exception:
    pass
" "$ENV_FILE" "$key"
}

# Helper function to check if line exists in .env
has_env_key() {
    local key="$1"
    python3 -c "
import sys
path, k = sys.argv[1], sys.argv[2]
try:
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip().startswith(k + '='):
                sys.exit(0)
except Exception:
    pass
sys.exit(1)
" "$ENV_FILE" "$key"
}

# Helper function to set or replace a key in .env (idempotent, never duplicates)
set_or_replace_var() {
    local key="$1"
    local val="$2"
    python3 -c "
import sys
path, k, v = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
except FileNotFoundError:
    lines = []

found = False
out = []
for line in lines:
    s = line.strip()
    if not found and (s.startswith(k + '=') or s.startswith('# ' + k + '=') or s.startswith('#' + k + '=')):
        out.append(f'{k}={v}\n')
        found = True
    elif found and s.startswith(k + '='):
        continue
    else:
        out.append(line)

if not found:
    if out and not out[-1].endswith('\n'):
        out[-1] += '\n'
    out.append(f'{k}={v}\n')

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(out)
" "$ENV_FILE" "$key" "$val"
    chmod 600 "$ENV_FILE"
}

# Check if a value is a placeholder
is_placeholder() {
    local val="$1"
    if [ -z "$val" ]; then
        return 0
    fi
    case "$val" in
        *FakePlaceholder*|*placeholder*|*your_*|*example_secure*|123456789|-1001234567890)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

echo ""
echo "Checking required environment variables..."

# ------------------------------------------------------------------------------
# 1. TELEGRAM_BOT_TOKEN
# ------------------------------------------------------------------------------
CURRENT_BOT_TOKEN="$(get_env_val 'TELEGRAM_BOT_TOKEN')"
if is_placeholder "$CURRENT_BOT_TOKEN"; then
    echo "[-] TELEGRAM_BOT_TOKEN is missing or still a placeholder."
    if [ -t 0 ]; then
        read -s -p "Enter real TELEGRAM_BOT_TOKEN (input hidden): " NEW_TOKEN
        echo ""
    else
        read -r NEW_TOKEN
    fi
    NEW_TOKEN="${NEW_TOKEN//$'\r'/}"
    NEW_TOKEN="${NEW_TOKEN#"${NEW_TOKEN%%[![:space:]]*}"}"
    NEW_TOKEN="${NEW_TOKEN%"${NEW_TOKEN##*[![:space:]]}"}"
    if [ -n "$NEW_TOKEN" ] && ! is_placeholder "$NEW_TOKEN"; then
        set_or_replace_var 'TELEGRAM_BOT_TOKEN' "$NEW_TOKEN"
        TOKEN_LEN=${#NEW_TOKEN}
        PREFIX="${NEW_TOKEN:0:4}"
        SUFFIX="${NEW_TOKEN: -4}"
        echo "  -> Saved TELEGRAM_BOT_TOKEN (${PREFIX}...${SUFFIX}, ${TOKEN_LEN} chars)."
    else
        echo "  [!] Warning: No valid bot token provided; left placeholder unchanged."
    fi
else
    TOKEN_LEN=${#CURRENT_BOT_TOKEN}
    PREFIX="${CURRENT_BOT_TOKEN:0:4}"
    SUFFIX="${CURRENT_BOT_TOKEN: -4}"
    echo "[+] TELEGRAM_BOT_TOKEN: Already configured (${PREFIX}...${SUFFIX}, ${TOKEN_LEN} chars)."
fi

# ------------------------------------------------------------------------------
# 2. TELEGRAM_USER_ID
# ------------------------------------------------------------------------------
CURRENT_USER_ID="$(get_env_val 'TELEGRAM_USER_ID')"
if [ "$CURRENT_USER_ID" = "$KNOWN_USER_ID" ]; then
    echo "[+] TELEGRAM_USER_ID: Already set to owner ID (${KNOWN_USER_ID})."
elif is_placeholder "$CURRENT_USER_ID"; then
    echo "[-] TELEGRAM_USER_ID is a placeholder (${CURRENT_USER_ID:-unset})."
    if [ -t 0 ]; then
        read -p "Use expected User ID [${KNOWN_USER_ID}] or enter custom: " USER_INPUT
    else
        read -r USER_INPUT || USER_INPUT=""
    fi
    USER_INPUT="${USER_INPUT//$'\r'/}"
    USER_INPUT="${USER_INPUT#"${USER_INPUT%%[![:space:]]*}"}"
    USER_INPUT="${USER_INPUT%"${USER_INPUT##*[![:space:]]}"}"
    TARGET_USER_ID="${USER_INPUT:-$KNOWN_USER_ID}"
    set_or_replace_var 'TELEGRAM_USER_ID' "$TARGET_USER_ID"
    echo "  -> Saved TELEGRAM_USER_ID=${TARGET_USER_ID}."
else
    echo "[!] Notice: TELEGRAM_USER_ID is currently '${CURRENT_USER_ID}' (expected: ${KNOWN_USER_ID})."
    if [ -t 0 ]; then
        read -p "Keep '${CURRENT_USER_ID}' [K] or replace with '${KNOWN_USER_ID}' [R]? " CHOICE
        if [[ "$CHOICE" =~ ^[Rr] ]]; then
            set_or_replace_var 'TELEGRAM_USER_ID' "$KNOWN_USER_ID"
            echo "  -> Replaced with TELEGRAM_USER_ID=${KNOWN_USER_ID}."
        else
            echo "  -> Kept existing TELEGRAM_USER_ID=${CURRENT_USER_ID}."
        fi
    fi
fi

# ------------------------------------------------------------------------------
# 3. TELEGRAM_GROUP_ID
# ------------------------------------------------------------------------------
CURRENT_GROUP_ID="$(get_env_val 'TELEGRAM_GROUP_ID')"
if [ "$CURRENT_GROUP_ID" = "$KNOWN_GROUP_ID" ]; then
    echo "[+] TELEGRAM_GROUP_ID: Already set to group ID (${KNOWN_GROUP_ID})."
elif is_placeholder "$CURRENT_GROUP_ID"; then
    echo "[-] TELEGRAM_GROUP_ID is a placeholder (${CURRENT_GROUP_ID:-unset})."
    if [ -t 0 ]; then
        read -p "Use expected Group ID [${KNOWN_GROUP_ID}] or enter custom: " GROUP_INPUT
    else
        read -r GROUP_INPUT || GROUP_INPUT=""
    fi
    GROUP_INPUT="${GROUP_INPUT//$'\r'/}"
    GROUP_INPUT="${GROUP_INPUT#"${GROUP_INPUT%%[![:space:]]*}"}"
    GROUP_INPUT="${GROUP_INPUT%"${GROUP_INPUT##*[![:space:]]}"}"
    TARGET_GROUP_ID="${GROUP_INPUT:-$KNOWN_GROUP_ID}"
    set_or_replace_var 'TELEGRAM_GROUP_ID' "$TARGET_GROUP_ID"
    echo "  -> Saved TELEGRAM_GROUP_ID=${TARGET_GROUP_ID}."
else
    echo "[!] Notice: TELEGRAM_GROUP_ID is currently '${CURRENT_GROUP_ID}' (expected: ${KNOWN_GROUP_ID})."
    if [ -t 0 ]; then
        read -p "Keep '${CURRENT_GROUP_ID}' [K] or replace with '${KNOWN_GROUP_ID}' [R]? " GCHOICE
        if [[ "$GCHOICE" =~ ^[Rr] ]]; then
            set_or_replace_var 'TELEGRAM_GROUP_ID' "$KNOWN_GROUP_ID"
            echo "  -> Replaced with TELEGRAM_GROUP_ID=${KNOWN_GROUP_ID}."
        else
            echo "  -> Kept existing TELEGRAM_GROUP_ID=${CURRENT_GROUP_ID}."
        fi
    fi
fi

# ------------------------------------------------------------------------------
# 4. GEMINI_API_KEY
# ------------------------------------------------------------------------------
CURRENT_GEMINI_KEY="$(get_env_val 'GEMINI_API_KEY')"
if ! has_env_key 'GEMINI_API_KEY' || is_placeholder "$CURRENT_GEMINI_KEY"; then
    if ! has_env_key 'GEMINI_API_KEY'; then
        echo "[-] GEMINI_API_KEY line is completely missing."
    else
        echo "[-] GEMINI_API_KEY is currently a placeholder."
    fi
    if [ -t 0 ]; then
        read -s -p "Enter real GEMINI_API_KEY (input hidden): " NEW_GEMINI_KEY
        echo ""
    else
        read -r NEW_GEMINI_KEY
    fi
    NEW_GEMINI_KEY="${NEW_GEMINI_KEY//$'\r'/}"
    NEW_GEMINI_KEY="${NEW_GEMINI_KEY#"${NEW_GEMINI_KEY%%[![:space:]]*}"}"
    NEW_GEMINI_KEY="${NEW_GEMINI_KEY%"${NEW_GEMINI_KEY##*[![:space:]]}"}"
    if [ -n "$NEW_GEMINI_KEY" ] && ! is_placeholder "$NEW_GEMINI_KEY"; then
        set_or_replace_var 'GEMINI_API_KEY' "$NEW_GEMINI_KEY"
        G_LEN=${#NEW_GEMINI_KEY}
        G_PREFIX="${NEW_GEMINI_KEY:0:4}"
        G_SUFFIX="${NEW_GEMINI_KEY: -4}"
        echo "  -> Saved GEMINI_API_KEY (${G_PREFIX}...${G_SUFFIX}, ${G_LEN} chars)."
    else
        echo "  [!] Warning: No valid Gemini key provided; key remains unconfigured."
    fi
else
    G_LEN=${#CURRENT_GEMINI_KEY}
    G_PREFIX="${CURRENT_GEMINI_KEY:0:4}"
    G_SUFFIX="${CURRENT_GEMINI_KEY: -4}"
    echo "[+] GEMINI_API_KEY: Already configured (${G_PREFIX}...${G_SUFFIX}, ${G_LEN} chars)."
fi

# ------------------------------------------------------------------------------
# 5. GEMINI_MODEL
# ------------------------------------------------------------------------------
CURRENT_MODEL="$(get_env_val 'GEMINI_MODEL')"
if [ -z "$CURRENT_MODEL" ]; then
    set_or_replace_var 'GEMINI_MODEL' "$DEFAULT_GEMINI_MODEL"
    echo "  -> Set GEMINI_MODEL=${DEFAULT_GEMINI_MODEL} (default)."
else
    echo "[+] GEMINI_MODEL: Already configured (${CURRENT_MODEL})."
fi

# ------------------------------------------------------------------------------
# Final Verification Summary (No secret values printed)
# ------------------------------------------------------------------------------
FINAL_TOKEN="$(get_env_val 'TELEGRAM_BOT_TOKEN')"
FINAL_UID="$(get_env_val 'TELEGRAM_USER_ID')"
FINAL_GID="$(get_env_val 'TELEGRAM_GROUP_ID')"
FINAL_GKEY="$(get_env_val 'GEMINI_API_KEY')"
FINAL_MODEL="$(get_env_val 'GEMINI_MODEL')"

echo ""
echo "======================================================================"
echo "  Configuration Verification Summary"
echo "======================================================================"

if is_placeholder "$FINAL_TOKEN"; then
    echo "  ❌ TELEGRAM_BOT_TOKEN : MISSING / PLACEHOLDER"
else
    echo "  ✅ TELEGRAM_BOT_TOKEN : OK (configured)"
fi

if is_placeholder "$FINAL_UID"; then
    echo "  ❌ TELEGRAM_USER_ID   : MISSING / PLACEHOLDER"
else
    echo "  ✅ TELEGRAM_USER_ID   : OK (${FINAL_UID})"
fi

if is_placeholder "$FINAL_GID"; then
    echo "  ❌ TELEGRAM_GROUP_ID  : MISSING / PLACEHOLDER"
else
    echo "  ✅ TELEGRAM_GROUP_ID  : OK (${FINAL_GID})"
fi

if is_placeholder "$FINAL_GKEY" || ! has_env_key 'GEMINI_API_KEY'; then
    echo "  ❌ GEMINI_API_KEY     : MISSING / PLACEHOLDER"
else
    echo "  ✅ GEMINI_API_KEY     : OK (configured)"
fi

if [ -z "$FINAL_MODEL" ]; then
    echo "  ❌ GEMINI_MODEL       : MISSING"
else
    echo "  ✅ GEMINI_MODEL       : OK (${FINAL_MODEL})"
fi

echo "======================================================================"
echo "Done. All other configuration keys in .env were preserved."
