"""
Central HTML Safety, Tag Sanitization, Message Splitting and Delivery Utilities.
Guarantees:
1. Safe escaping of dynamic user, merchant, category, and OCR inputs.
2. Allowlisted formatting for Gemini outputs (<b>, <i>, <code> only).
3. Splitting messages exceeding Telegram's 4096 character limit.
4. Graceful plain-text fallback when Telegram rejects malformed HTML markup.
"""

import html
import re
from typing import Any, List, Optional
from telegram.error import BadRequest
from config import logger

# Telegram HTML allowed tags for rich text
ALLOWED_HTML_TAGS = {"b", "strong", "i", "em", "code", "pre", "blockquote", "a", "u", "s", "del", "tg-spoiler"}

# Regex matching any HTML opening, closing, or self-closing tag
HTML_TAG_RE = re.compile(r"</?([a-zA-Z0-9_-]+)(?:\s+[^>]*?)?/?>")


def escape_html(value: Any) -> str:
    """Escapes HTML special characters (&, <, >, ") in arbitrary dynamic values."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def strip_html_tags(text: str) -> str:
    """Strips all HTML tags to produce clean plain text."""
    if not text:
        return ""
    # Replace <br> or </p> with newline
    t = re.sub(r"(?i)<br\s*/?>|</p>", "\n", text)
    # Strip remaining tags
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def sanitize_gemini_html(text: str) -> str:
    """
    Sanitizes AI-generated text to allow ONLY safe, verified Telegram HTML tags
    (b, i, em, strong, code, blockquote). Any unsupported or malformed tags are escaped.
    """
    if not text:
        return ""

    # Check if text contains tags
    def replace_tag(match: re.Match) -> str:
        full_tag = match.group(0)
        tag_name = match.group(1).lower()
        if tag_name in {"b", "strong", "i", "em", "code", "blockquote"}:
            # Keep standard simple tags
            if full_tag.startswith("</"):
                return f"</{tag_name}>"
            return f"<{tag_name}>"
        # Escape any disallowed tag
        return html.escape(full_tag)

    return HTML_TAG_RE.sub(replace_tag, text)


def split_message(text: str, max_length: int = 4096) -> List[str]:
    """
    Splits a message into chunks not exceeding Telegram's 4096 character limit,
    preferring splits along newlines and whitespace.
    """
    if not text:
        return [""]
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while len(remaining) > max_length:
        # Try to find split point on newline within max_length
        split_idx = remaining.rfind("\n", 0, max_length)
        if split_idx == -1 or split_idx < max_length // 2:
            # Fall back to space split
            split_idx = remaining.rfind(" ", 0, max_length)
        if split_idx == -1 or split_idx < max_length // 3:
            # Hard cutoff if no reasonable whitespace
            split_idx = max_length

        chunk = remaining[:split_idx].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_idx:].lstrip()

    if remaining:
        chunks.append(remaining)

    return chunks


async def send_safe_message(
    bot,
    chat_id: int | str,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
    **kwargs
):
    """
    Sends a message safely to Telegram:
    - Splits text over 4096 chars.
    - If Telegram rejects HTML parsing, falls back to plain text.
    """
    chunks = split_message(text, max_length=4096)
    last_msg = None

    for i, chunk in enumerate(chunks):
        # Attach reply_markup only to the final chunk
        markup = reply_markup if i == len(chunks) - 1 else None
        try:
            last_msg = await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                reply_markup=markup,
                parse_mode=parse_mode,
                **kwargs
            )
        except BadRequest as br_err:
            logger.warning(f"Telegram rejected formatted message ({br_err}). Falling back to plain text...")
            plain_text = strip_html_tags(chunk)
            last_msg = await bot.send_message(
                chat_id=chat_id,
                text=plain_text,
                reply_markup=markup,
                **kwargs
            )
        except Exception as err:
            logger.error(f"Error sending message to {chat_id}: {err}")
            raise err

    return last_msg


async def edit_safe_message(
    message_or_query,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML"
):
    """
    Edits a message or callback query message safely with plain text fallback on BadRequest.
    """
    try:
        if hasattr(message_or_query, "edit_text"):
            return await message_or_query.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        elif hasattr(message_or_query, "edit_message_text"):
            return await message_or_query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
    except BadRequest as br_err:
        logger.warning(f"Telegram rejected message edit with parse_mode={parse_mode} ({br_err}). Falling back to plain text...")
        plain_text = strip_html_tags(text)
        if hasattr(message_or_query, "edit_text"):
            return await message_or_query.edit_text(
                text=plain_text,
                reply_markup=reply_markup
            )
        elif hasattr(message_or_query, "edit_message_text"):
            return await message_or_query.edit_message_text(
                text=plain_text,
                reply_markup=reply_markup
            )
