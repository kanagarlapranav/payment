"""
Dashboard Authentication and Session Management Service.

Security Architecture:
1. One-Time Login Codes:
   - Generated exclusively via owner-only Telegram /dashboard command.
   - Valid for 60 seconds, strictly single-use.
   - Compared using hmac.compare_digest on bytes (non-ASCII safe).
2. Session Cookie:
   - Exchanged via GET /auth?code=...
   - HttpOnly, SameSite=Strict, Secure (over HTTPS or production), 30-minute validity.
   - Browser redirects to /dashboard with code completely removed from URL.
   - Stored in-memory (server restarts require running /dashboard again).
3. Rate Limiting:
   - In-memory rate limiting on failed auth attempts per client IP (max 5 failures per 5 minutes).
4. Security Headers:
   - CSP, X-Content-Type-Options: nosniff, Referrer-Policy: no-referrer, Cache-Control: no-store on APIs.
"""

import time
import secrets
import hmac
import os
from http.cookies import SimpleCookie
from typing import Optional, Tuple, Dict, Any

from config import logger

# In-memory storage for active single-use auth codes and sessions
# Structure: code -> {"created_at": float, "used": bool}
_AUTH_CODES: Dict[str, Dict[str, Any]] = {}

# Structure: session_id -> {"created_at": float, "expires_at": float}
_SESSIONS: Dict[str, Dict[str, Any]] = {}

# Structure: ip_address -> [timestamp, timestamp, ...]
_FAILED_LOGINS: Dict[str, list[float]] = {}

# Expiry Constants
CODE_EXPIRY_SECONDS = 60.0        # 1 minute single-use validity
SESSION_EXPIRY_SECONDS = 1800.0    # 30 minutes session lifetime
MAX_FAILED_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 300.0  # 5 minutes window


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
    
    # Prune auth codes older than 5 minutes
    expired_codes = [c for c, data in _AUTH_CODES.items() if now - data.get("created_at", 0) > 300.0]
    for c in expired_codes:
        _AUTH_CODES.pop(c, None)

    # Prune expired sessions
    expired_sessions = [s for s, data in _SESSIONS.items() if now > data.get("expires_at", 0)]
    for s in expired_sessions:
        _SESSIONS.pop(s, None)

    # Prune old failed login timestamps
    for ip in list(_FAILED_LOGINS.keys()):
        _FAILED_LOGINS[ip] = [t for t in _FAILED_LOGINS[ip] if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if not _FAILED_LOGINS[ip]:
            _FAILED_LOGINS.pop(ip, None)


def create_one_time_code() -> str:
    """
    Generates a cryptographically secure 32-character one-time login code.
    Valid for 60 seconds, single-use only.
    """
    cleanup_expired()
    code = secrets.token_urlsafe(32)
    _AUTH_CODES[code] = {
        "created_at": time.time(),
        "used": False
    }
    logger.info("Generated new single-use 60s dashboard auth code.")
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
    Exchanges a single-use one-time code for a 30-minute session cookie.
    
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

    # Find matching code entry using timing-safe comparison
    matching_code_key = None
    for stored_code in list(_AUTH_CODES.keys()):
        if compare_secrets(stored_code, code):
            matching_code_key = stored_code
            break

    if not matching_code_key:
        record_failed_attempt(client_ip)
        return False, "Invalid login code. Please generate a new one via /dashboard in Telegram.", ""

    code_data = _AUTH_CODES.pop(matching_code_key, None)
    now = time.time()

    if not code_data or code_data.get("used") or (now - code_data.get("created_at", 0) > CODE_EXPIRY_SECONDS):
        record_failed_attempt(client_ip)
        return False, "Login code has expired or was already used. Please request a new code via /dashboard in Telegram.", ""

    # Generate session ID
    session_id = secrets.token_hex(32)
    _SESSIONS[session_id] = {
        "created_at": now,
        "expires_at": now + SESSION_EXPIRY_SECONDS
    }

    # Reset failed attempts for this IP on successful auth
    if client_ip in _FAILED_LOGINS:
        _FAILED_LOGINS.pop(client_ip, None)

    # Build HttpOnly, SameSite=Strict session cookie
    secure_flag = "; Secure" if is_https or os.getenv("ENVIRONMENT") == "production" or os.getenv("RENDER") else ""
    cookie_header = f"session_id={session_id}; Path=/; Max-Age={int(SESSION_EXPIRY_SECONDS)}; HttpOnly; SameSite=Strict{secure_flag}"
    
    logger.info("Successfully exchanged one-time code for 30-minute session cookie.")
    return True, session_id, cookie_header


def validate_session(cookie_header: Optional[str]) -> bool:
    """
    Validates the session_id from the incoming HTTP request's Cookie header.
    Returns True if the session exists and has not expired.
    """
    cleanup_expired()
    if not cookie_header:
        return False

    try:
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        if "session_id" not in cookie:
            return False

        supplied_session = cookie["session_id"].value
        if not supplied_session:
            return False

        # Match against active sessions
        for active_sid, data in list(_SESSIONS.items()):
            if compare_secrets(active_sid, supplied_session):
                if time.time() < data.get("expires_at", 0):
                    return True
                else:
                    _SESSIONS.pop(active_sid, None)
                    return False
        return False
    except Exception as e:
        logger.debug(f"Session validation notice: {e}")
        return False


def get_security_headers(is_api: bool = False) -> Dict[str, str]:
    """Returns the mandatory security headers for all web and API responses."""
    headers = {
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
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
