import os
import base64
import json
import requests
import io
from PIL import Image
from config import GEMINI_API_KEY, GEMINI_MODEL, logger
from database.models import Transaction
from utils.dates import parse_date

FALLBACK_GEMINI_KEY = ""
REVOKED_LEAKED_KEY = "AIzaSyBxSq2mRHzoayVdItKrQqTC-4UIGqXiU8E"
GEMINI_MODELS = ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-flash-latest"]

def get_effective_gemini_api_key() -> str:
    """
    Returns valid Gemini API Key.
    If missing or set to the revoked leaked key, returns empty string.
    """
    import ocr.gemini_vision as gv
    key = getattr(gv, 'GEMINI_API_KEY', None)
    fb = getattr(gv, 'FALLBACK_GEMINI_KEY', '')
    if key is None or key == "DISABLED" or key is False:
        return fb or ''
    key = str(key).strip()
    if not key or key == REVOKED_LEAKED_KEY or key.startswith('gen-lang-client'):
        return fb or ''
    return key



def is_gemini_available() -> bool:
    """Returns True if Gemini API key is configured."""
    return bool(get_effective_gemini_api_key())

def _prepare_image_b64(image_path: str) -> tuple[str, str]:
    """Crops status bar/chat bars and compresses image in memory to max 800px for lightning-fast API upload."""
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            w, h = img.size
            # If tall phone screenshot, crop top 8% (status bar) and bottom 15% (chat UI)
            if h > w * 1.3:
                top = int(h * 0.08)
                bottom = int(h * 0.85)
                img = img.crop((0, top, w, bottom))

            img.thumbnail((800, 800), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            return b64, "image/jpeg"
    except Exception as img_err:
        logger.warning(f"Could not resize image with PIL ({img_err}), using raw file.")
        with open(image_path, "rb") as f:
            raw_b64 = base64.b64encode(f.read()).decode("utf-8")
        ext = os.path.splitext(image_path)[1].lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        return raw_b64, mime

def extract_transaction_with_gemini(image_path: str, caption: str = "") -> tuple[Transaction | None, int]:
    """
    Analyzes payment receipt screenshot using Google Gemini Vision API.
    Uses thumbnail compression, receipt cropping, and multi-model fallback for maximum speed and reliability.
    Returns (Transaction, confidence_score) or (None, 0) if failed/unavailable.
    """
    api_key = get_effective_gemini_api_key()
    if not api_key:
        return None, 0

    if not os.path.exists(image_path):
        logger.warning(f"Gemini Vision: Image path not found: {image_path}")
        return None, 0

    try:
        image_b64, mime_type = _prepare_image_b64(image_path)

        prompt = (
            "You are an expert Indian UPI & Banking Receipt OCR extractor.\n"
            "Analyze this payment receipt screenshot (from apps like Super.money, Google Pay, PhonePe, Paytm, CRED, BHIM, Amazon Pay, Navi, YONO SBI, etc.).\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Extract details ONLY from the main payment receipt at the TOP (e.g. amount ₹20 under 'Payment Successful').\n"
            "2. IGNORE any chat history, previous Telegram bot reply bubbles, or URLs at the bottom of the image.\n"
            "3. Return ONLY a valid JSON object with the following fields:\n"
            "- amount (float: exact bill amount paid/received e.g. 20.0, strictly positive)\n"
            "- transaction_type ('SENT' if money was sent/paid/debited, 'RECEIVED' if credited)\n"
            "- person_name (string: name of the recipient or sender, e.g. 'VIKRAMAN NAIR K')\n"
            "- payment_app (string: 'Super.money', 'Google Pay', 'PhonePe', 'Paytm', 'CRED', 'Amazon Pay', 'Navi', 'Generic')\n"
            "- transaction_date (string: 'YYYY-MM-DD' or formatted date)\n"
            "- transaction_time (string: e.g. '1:41 PM')\n"
            "- bank_name (string: bank name if visible, e.g. 'Federal Bank', 'YES BANK')\n"
            "- reference_number (string: 12-digit UTR / UPI Ref ID e.g. '662474885797')\n"
            "- raw_text (string: text content)\n"
            "- confidence (integer 90-100)\n\n"
            f"Optional user caption: '{caption}'\n"
            "Return ONLY the JSON object, without markdown code fences or quotes."
        )

        models_to_try = list(GEMINI_MODELS)
        if GEMINI_MODEL and GEMINI_MODEL not in models_to_try:
            models_to_try.insert(0, GEMINI_MODEL)

        last_error = None
        for model_name in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": image_b64
                                }
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "response_mime_type": "application/json",
                    "temperature": 0.1
                }
            }

            try:
                logger.info(f"Invoking Gemini Vision API ({model_name}) for {image_path}...")
                response = requests.post(url, json=payload, timeout=6.0)


                if response.status_code == 200:
                    data = response.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        continue
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        continue

                    json_str = parts[0].get("text", "").strip()
                    if json_str.startswith("```"):
                        json_str = json_str.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

                    parsed = json.loads(json_str)
                    amount = float(parsed.get("amount") or 0.0)
                    tx_type = str(parsed.get("transaction_type") or "").upper()
                    if tx_type not in ("SENT", "RECEIVED"):
                        tx_type = "SENT"

                    person = parsed.get("person_name") or "Unknown"
                    payment_app = parsed.get("payment_app") or "Generic"
                    raw_date = parsed.get("transaction_date")
                    date_obj = parse_date(str(raw_date)) if raw_date else parse_date("Today")
                    
                    time_str = parsed.get("transaction_time") or ""
                    bank = parsed.get("bank_name") or ""
                    ref_no = str(parsed.get("reference_number") or "").strip()
                    if ref_no.lower() in ("null", "none", "n/a", ""):
                        ref_no = None

                    confidence = int(parsed.get("confidence") or 95)
                    if amount > 0 and person != "Unknown":
                        confidence = max(confidence, 90)

                    transaction = Transaction(
                        transaction_type=tx_type,
                        amount=amount,
                        person_name=person,
                        sender_name=person if tx_type == "RECEIVED" else "",
                        recipient_name=person if tx_type == "SENT" else "",
                        payment_app=payment_app,
                        transaction_date=date_obj,
                        transaction_time=time_str,
                        bank_name=bank,
                        reference_number=ref_no or "",
                        ocr_text=parsed.get("raw_text") or json_str,
                        payment_status="SUCCESS"
                    )

                    logger.info(f"Gemini Vision ({model_name}) successfully parsed receipt: {tx_type} Rs. {amount} to/from {person} (Confidence: {confidence}%)")
                    return transaction, confidence
                else:
                    logger.warning(f"Gemini model {model_name} returned status {response.status_code}: {response.text[:200]}")
                    last_error = f"Status {response.status_code}"
                    if response.status_code in (401, 403):
                        logger.error("Gemini API key rejected (401/403). Halting further model attempts.")
                        break
            except requests.RequestException as req_err:
                logger.warning(f"Gemini model {model_name} request failed/timed out: {req_err}")
                last_error = str(req_err)
                continue

        logger.warning(f"All Gemini Vision models failed or timed out: {last_error}")
        return None, 0

    except Exception as e:
        logger.error(f"Error during Gemini Vision processing: {e}", exc_info=True)
        return None, 0

def parse_text_with_gemini(text: str) -> tuple[Transaction | None, int]:
    """
    Parses natural language transaction text using Gemini AI.
    Example: 'Paid 120 at cafeteria for dosa and coffee yesterday'
    """
    api_key = get_effective_gemini_api_key()
    if not api_key or not text or len(text.strip()) < 3:
        return None, 0

    prompt = (
        "You are an expert financial assistant. Parse this transaction message:\n"
        f"Message: '{text}'\n\n"
        "Return ONLY a JSON object with:\n"
        "- is_transaction (boolean: true if this describes a financial payment/income, false otherwise)\n"
        "- amount (float: strictly positive)\n"
        "- transaction_type ('SENT' or 'RECEIVED')\n"
        "- person_name (string: person/merchant name or 'Unknown')\n"
        "- category (string: 'Food & Dining', 'Groceries', 'Utilities', 'Transportation', 'Shopping', 'Entertainment', 'Transfers', 'Health', 'Income', 'General')\n"
        "- transaction_date (string: 'YYYY-MM-DD' or relative like 'today', 'yesterday')\n"
        "- note (string: brief note of what was bought or reason)"
    )

    for model_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}
        }
        try:
            res = requests.post(url, json=payload, timeout=8.0)
            if res.status_code == 200:
                data = res.json()
                parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                if not parts:
                    continue
                js = parts[0].get("text", "").strip()
                if js.startswith("```"):
                    js = js.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                p = json.loads(js)
                if not p.get("is_transaction"):
                    return None, 0
                amt = float(p.get("amount") or 0.0)
                if amt <= 0:
                    return None, 0
                tx_type = str(p.get("transaction_type") or "SENT").upper()
                person = str(p.get("person_name") or "Unknown").title()
                cat = str(p.get("category") or "General")
                raw_d = str(p.get("transaction_date") or "today")
                d_obj = parse_date(raw_d)
                
                tx = Transaction(
                    transaction_type=tx_type,
                    amount=amt,
                    person_name=person,
                    category=cat,
                    transaction_date=d_obj,
                    ocr_text=text,
                    payment_status="SUCCESS"
                )
                return tx, 95
        except Exception:
            continue
    return None, 0

def generate_gemini_spending_advice(metrics_summary: str) -> str:
    """Generates friendly, personalized financial coaching and savings tips using Gemini AI."""
    api_key = get_effective_gemini_api_key()
    if not api_key:
        return ""

    prompt = (
        "You are an encouraging, smart personal financial advisor.\n"
        "Review these monthly spending metrics and give 2-3 short, bulleted, actionable pieces of advice/observations.\n"
        "Keep it concise, friendly, and formatted in HTML with emojis (e.g. 💡, 🎯, 🚀). Max 4 lines.\n\n"
        f"Metrics:\n{metrics_summary}"
    )

    for model_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3}
        }
        try:
            res = requests.post(url, json=payload, timeout=8.0)
            if res.status_code == 200:
                parts = res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "").strip()
        except Exception:
            continue
    return ""

def generate_gemini_daily_commentary(daily_summary: str) -> str:
    """Generates a smart 2-sentence closing commentary for the daily digest."""
    api_key = get_effective_gemini_api_key()
    if not api_key:
        return ""

    prompt = (
        "You are a friendly personal money tracker.\n"
        "Provide a 1-2 sentence friendly, motivating closing remark about today's spending in HTML with emojis.\n\n"
        f"Daily Data:\n{daily_summary}"
    )

    for model_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3}
        }
        try:
            res = requests.post(url, json=payload, timeout=8.0)
            if res.status_code == 200:
                parts = res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "").strip()
        except Exception:
            continue
    return ""
