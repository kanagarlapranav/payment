"""
Dashboard Authentication and Session Management Service.

Security Architecture:
1. One-Time Login Codes:
   - Generated exclusively via owner/admin Telegram /dashboard command.
   - Valid for 60 seconds, strictly single-use.
   - Stored hashed in SQLite (dashboard_auth_codes) and in-memory cache.
2. Persistent Session Cookie:
   - Exchanged via GET /auth?code=...
   - HttpOnly, SameSite=Strict, Secure (over HTTPS or production), 30-minute validity.
   - Stored hashed in SQLite (dashboard_sessions) and in-memory cache.
   - Live membership & role revalidated against workspace_members on access.
3. Rate Limiting:
   - In-memory rate limiting on failed auth attempts per client IP (max 5 failures per 5 minutes).
4. Security Headers:
   - CSP, X-Content-Type-Options: nosniff, Referrer-Policy: no-referrer, Cache-Control: no-store on APIs.
"""

import time
import secrets
import hmac
import hashlib
import os
from http.cookies import SimpleCookie
from typing import Optional, Tuple, Dict, Any

from config import logger
from database.db import get_db_connection, LEDGER_LOCK

# In-memory fast cache
_AUTH_CODES: Dict[str, Dict[str, Any]] = {}
_SESSIONS: Dict[str, Dict[str, Any]] = {}
_FAILED_LOGINS: Dict[str, list[float]] = {}

# Expiry Constants
CODE_EXPIRY_SECONDS = 60.0         # 60 seconds validity
SESSION_EXPIRY_SECONDS = 1800.0    # 30 minutes session lifetime
MAX_FAILED_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 300.0  # 5 minutes window


def _hash_val(val: str) -> str:
    """Computes SHA-256 hash of a code or session ID."""
    return hashlib.sha256(val.strip().encode("utf-8")).hexdigest()


def compare_secrets(a: str | bytes, b: str | bytes) -> bool:
    """Non-ASCII safe constant-time string comparison using hmac.compare_digest on bytes."""
    if not a or not b:
        return False
    b_a = a.encode("utf-8") if isinstance(a, str) else a
    b_b = b.encode("utf-8") if isinstance(b, str) else b
    return hmac.compare_digest(b_a, b_b)


def cleanup_expired():
    """Prunes expired auth codes, expired sessions, and old rate limit timestamps."""
    now = time.time()
    
    # Prune auth codes older than 10 minutes from memory
    expired_codes = [c for c, data in _AUTH_CODES.items() if now - data.get("created_at", 0) > 600.0]
    for c in expired_codes:
        _AUTH_CODES.pop(c, None)

    # Prune expired sessions from memory
    expired_sessions = [s for s, data in _SESSIONS.items() if now > data.get("expires_at", 0)]
    for s in expired_sessions:
        _SESSIONS.pop(s, None)

    # Prune old failed login timestamps
    for ip in list(_FAILED_LOGINS.keys()):
        _FAILED_LOGINS[ip] = [t for t in _FAILED_LOGINS[ip] if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if not _FAILED_LOGINS[ip]:
            _FAILED_LOGINS.pop(ip, None)


def create_one_time_code(user_id: Optional[int] = None, workspace_id: Optional[str] = None, role: str = "member") -> str:
    """
    Generates a cryptographically secure 32-character one-time login code.
    Valid for 60 seconds, single-use only.
    Persists hashed code to SQLite database and in-memory cache.
    """
    cleanup_expired()
    from config import TELEGRAM_USER_ID
    if not workspace_id:
        try:
            from database.queries import get_default_workspace_id
            workspace_id = get_default_workspace_id()
        except Exception:
            workspace_id = "default"
    else:
        workspace_id = str(workspace_id)

    if user_id is None:
        user_id = int(TELEGRAM_USER_ID or 1)

    code = secrets.token_urlsafe(32)
    code_h = _hash_val(code)
    now = time.time()
    expires_at = now + CODE_EXPIRY_SECONDS

    # 1. In-memory cache
    _AUTH_CODES[code] = {
        "created_at": now,
        "used": False,
        "user_id": user_id,
        "workspace_id": workspace_id,
        "role": role
    }

    # 2. Database persistence
    try:
        with LEDGER_LOCK:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO dashboard_auth_codes (
                        code_hash, workspace_id, user_id, role, expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (code_h, str(workspace_id or ""), int(user_id or 0), str(role or "member"), expires_at, now))
                conn.commit()
    except Exception as e:
        logger.warning(f"Could not persist dashboard auth code to DB: {e}")

    logger.info(f"Generated new persistent single-use dashboard auth code (workspace_id={workspace_id}, user_id={user_id}, role={role}).")
    return code


def is_rate_limited(client_ip: str) -> bool:
    """Checks if a client IP has exceeded the allowed failed authentication attempts."""
    cleanup_expired()
    if not client_ip:
        return False
    recent_failures = _FAILED_LOGINS.get(client_ip, [])
    return len(recent_failures) >= MAX_FAILED_ATTEMPTS


def record_failed_attempt(client_ip: str):
    """Records a failed authentication attempt for rate limiting."""
    if not client_ip:
        return
    now = time.time()
    if client_ip not in _FAILED_LOGINS:
        _FAILED_LOGINS[client_ip] = []
    _FAILED_LOGINS[client_ip].append(now)
    logger.warning(f"Failed dashboard login attempt recorded for IP: {client_ip} ({len(_FAILED_LOGINS[client_ip])}/{MAX_FAILED_ATTEMPTS})")


def exchange_code_for_session(code: str, client_ip: str = "", is_https: bool = False) -> Tuple[bool, str, str]:
    """
    Exchanges a single-use one-time code for a 30-minute persistent session cookie.
    Transfers associated workspace_id, user_id, role, and issues a CSRF token.
    
    Returns:
        (success: bool, session_or_error: str, cookie_header: str)
    """
    cleanup_expired()

    if is_rate_limited(client_ip):
        logger.warning(f"Dashboard auth rejected: IP {client_ip} is rate limited.")
        return False, "Too many failed login attempts. Please wait 5 minutes.", ""

    if not code:
        record_failed_attempt(client_ip)
        return False, "Missing authentication code.", ""

    code_h = _hash_val(code)
    now = time.time()

    code_data = None
    # 1. Check in-memory
    matching_code_key = None
    for stored_code in list(_AUTH_CODES.keys()):
        if compare_secrets(stored_code, code):
            matching_code_key = stored_code
            break

    code_data = None
    if matching_code_key:
        code_data = _AUTH_CODES.pop(matching_code_key, None)

    if code_data and (code_data.get("used") or (now - code_data.get("created_at", 0) > CODE_EXPIRY_SECONDS)):
        record_failed_attempt(client_ip)
        return False, "Invalid login code: code has expired or was already used. Please request a new code via /dashboard in Telegram.", ""

    # 2. Check DB to ensure single-use and persistence
    db_code_row = None
    consumed = False
    try:
        with LEDGER_LOCK:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT workspace_id, user_id, role, expires_at, used_at
                    FROM dashboard_auth_codes
                    WHERE code_hash = ?
                """, (code_h,))
                db_code_row = cursor.fetchone()
                if db_code_row:
                    if db_code_row['used_at'] is None and now <= db_code_row['expires_at']:
                        cursor.execute("""
                            UPDATE dashboard_auth_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL
                        """, (now, code_h))
                        consumed = (cursor.rowcount == 1)
                        conn.commit()
    except Exception as e:
        logger.warning(f"Error querying dashboard_auth_codes from DB: {e}")

    if db_code_row:
        if not consumed or db_code_row['used_at'] is not None or now > db_code_row['expires_at']:
            record_failed_attempt(client_ip)
            return False, "Invalid login code: code has expired or was already used. Please request a new code via /dashboard in Telegram.", ""
        user_id = db_code_row['user_id']
        workspace_id = db_code_row['workspace_id']
        role = db_code_row['role'] or "member"
    elif code_data and not code_data.get("used") and (now - code_data.get("created_at", 0) <= CODE_EXPIRY_SECONDS):
        user_id = code_data.get("user_id")
        workspace_id = code_data.get("workspace_id")
        role = code_data.get("role", "member")
    else:
        record_failed_attempt(client_ip)
        return False, "Invalid login code. Please generate a new one via /dashboard in Telegram.", ""

    # Generate session ID and CSRF token
    session_id = secrets.token_hex(32)
    session_h = _hash_val(session_id)
    csrf_token = secrets.token_hex(16)
    expires_at = now + SESSION_EXPIRY_SECONDS

    # Save to memory
    _SESSIONS[session_id] = {
        "created_at": now,
        "expires_at": expires_at,
        "user_id": user_id,
        "workspace_id": workspace_id,
        "role": role,
        "csrf_token": csrf_token
    }

    # Save to DB
    try:
        with LEDGER_LOCK:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO dashboard_sessions (
                        session_id_hash, workspace_id, user_id, role, csrf_token, expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (session_h, str(workspace_id or ""), int(user_id or 0), role, csrf_token, expires_at, now))
                conn.commit()
    except Exception as e:
        logger.warning(f"Error persisting session to DB: {e}")

    # Reset failed attempts for this IP on successful auth
    if client_ip in _FAILED_LOGINS:
        _FAILED_LOGINS.pop(client_ip, None)

    # Build HttpOnly session cookie
    secure_flag = "; Secure" if is_https or os.getenv("ENVIRONMENT") == "production" or os.getenv("RENDER") else ""
    cookie_header = f"session_id={session_id}; Path=/; Max-Age={int(SESSION_EXPIRY_SECONDS)}; HttpOnly; SameSite=Lax{secure_flag}"
    
    logger.info(f"Successfully exchanged code for session {session_id[:8]}... (workspace_id={workspace_id}, role={role}).")
    return True, session_id, cookie_header


def validate_dashboard_action(
    session_info: Optional[Dict[str, Any]],
    required_role: str = "viewer",
    csrf_token_header: Optional[str] = None,
    is_mutation: bool = False
) -> Tuple[bool, str]:
    """
    Validates role authorization and anti-CSRF token for dashboard API requests.
    Returns (authorized: bool, error_message: str).
    """
    if not session_info:
        return False, "Unauthorized session. Please login via /dashboard in Telegram."

    role = session_info.get("role", "viewer")
    from bot.auth import WORKSPACE_ROLE_HIERARCHY
    caller_level = WORKSPACE_ROLE_HIERARCHY.get(role, 1)
    req_level = WORKSPACE_ROLE_HIERARCHY.get(required_role, 1)

    if caller_level < req_level:
        return False, f"Forbidden: This action requires '{required_role.upper()}' role (your role: '{role.title()}')."

    if is_mutation:
        expected_csrf = session_info.get("csrf_token")
        if not expected_csrf or not csrf_token_header or not compare_secrets(expected_csrf, csrf_token_header):
            return False, "Invalid or missing CSRF token (X-CSRF-Token)."

    return True, ""


def get_session_info(session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Returns session metadata dictionary (user_id, workspace_id, created_at, expires_at, role, csrf_token).
    Validates against in-memory cache and persistent SQLite store, and revalidates live membership.
    """
    cleanup_expired()
    if not session_id:
        return None

    now = time.time()
    data = None

    # Check memory cache
    for active_sid, sdata in list(_SESSIONS.items()):
        if compare_secrets(active_sid, session_id):
            if now < sdata.get("expires_at", 0):
                data = dict(sdata)
            else:
                _SESSIONS.pop(active_sid, None)
            break

    # If not found in cache, check database
    if not data:
        session_h = _hash_val(session_id)
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT workspace_id, user_id, role, csrf_token, expires_at, created_at, revoked_at
                    FROM dashboard_sessions
                    WHERE session_id_hash = ?
                """, (session_h,))
                row = cursor.fetchone()
                if row:
                    if row['revoked_at'] is None and now < row['expires_at']:
                        data = {
                            "created_at": row['created_at'],
                            "expires_at": row['expires_at'],
                            "user_id": row['user_id'],
                            "workspace_id": row['workspace_id'],
                            "role": row['role'],
                            "csrf_token": row['csrf_token']
                        }
                        _SESSIONS[session_id] = data
        except Exception as e:
            logger.debug(f"DB session lookup notice: {e}")

    if not data:
        return None

    # Revalidate live membership & status
    ws_id = data.get("workspace_id")
    uid = data.get("user_id")
    if not ws_id or uid is None:
        logger.warning(f"Rejecting unbound dashboard session (ws_id={ws_id}, uid={uid})")
        revoke_session(session_id)
        return None

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, is_active FROM workspaces WHERE id = ?", (str(ws_id),))
            ws_row = cursor.fetchone()
            if not ws_row or not ws_row['is_active']:
                logger.warning(f"Revoking session for inactive or missing workspace {ws_id}")
                revoke_session(session_id)
                return None

            cursor.execute("""
                SELECT role, is_active, status FROM workspace_members
                WHERE workspace_id = ? AND telegram_user_id = ?
            """, (str(ws_id), int(uid)))
            mem = cursor.fetchone()
            if mem:
                if not mem['is_active'] or mem['status'] in ('suspended', 'removed', 'revoked'):
                    # Revoke session if user has been deactivated or removed from existing workspace
                    revoke_session(session_id)
                    return None
                data['role'] = mem['role']
    except Exception as e:
        logger.debug(f"Live membership revalidation notice: {e}")

    return data


def revoke_session(session_id: Optional[str]) -> bool:
    """Revokes an active session in both memory and database."""
    if not session_id:
        return False
    _SESSIONS.pop(session_id, None)
    session_h = _hash_val(session_id)
    try:
        with LEDGER_LOCK:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE dashboard_sessions SET revoked_at = ? WHERE session_id_hash = ?
                """, (time.time(), session_h))
                conn.commit()
        return True
    except Exception as e:
        logger.warning(f"Error revoking session in DB: {e}")
        return False


def validate_session_id(session_id: Optional[str]) -> bool:
    """Returns True if the session exists and has not expired."""
    info = get_session_info(session_id)
    return info is not None


def get_session_info_from_cookie(cookie_header: Optional[str], session_param: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Extracts session ID from Cookie header or query param fallback and returns its session info."""
    sid = None
    if cookie_header:
        try:
            cookie = SimpleCookie()
            cookie.load(cookie_header)
            if "session_id" in cookie:
                sid = cookie["session_id"].value
        except Exception:
            pass

    if not sid and session_param:
        sid = session_param

    return get_session_info(sid) if sid else None


def validate_session(cookie_header: Optional[str]) -> bool:
    """Validates session from Cookie header."""
    info = get_session_info_from_cookie(cookie_header)
    return info is not None


def get_security_headers(is_api: bool = False) -> Dict[str, str]:
    """Returns the mandatory security headers for all web and API responses."""
    headers = {
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://telegram.org; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        ),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY"
    }
    if is_api:
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        headers["Pragma"] = "no-cache"
    return headers
