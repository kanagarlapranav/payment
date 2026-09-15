import os
import base64
import json
import requests
from config import GEMINI_API_KEY, GEMINI_MODEL, logger
from database.models import Transaction
from utils.dates import parse_date

def is_gemini_available() -> bool:
    """Returns True if Gemini API key is configured."""
    return bool(GEMINI_API_KEY and GEMINI_API_KEY.strip())

def extract_transaction_with_gemini(image_path: str, caption: str = "") -> tuple[Transaction | None, int]:
    """
    Analyzes payment receipt screenshot using Google Gemini 1.5 Flash Vision API.
    Returns (Transaction, confidence_score) or (None, 0) if failed/unavailable.
    """
    if not is_gemini_available():
        return None, 0

    if not os.path.exists(image_path):
        logger.warning(f"Gemini Vision: Image path not found: {image_path}")
        return None, 0

    try:
        # Read and base64-encode the image
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        # Determine MIME type
        ext = os.path.splitext(image_path)[1].lower()
        mime_type = "image/png" if ext == ".png" else "image/jpeg"

        prompt = (
            "Analyze this Indian payment / UPI receipt screenshot (from apps like PhonePe, Google Pay, Paytm, Amazon Pay, CRED, BHIM, YONO SBI, etc.).\n"
            "Extract the exact transaction details and return ONLY a valid JSON object with the following fields:\n"
            "- amount (number/float, e.g. 400.0 or 5000.0, strictly positive)\n"
            "- transaction_type ('SENT' if money was paid/debited/sent, 'RECEIVED' if money was credited/received/added)\n"
            "- person_name (string: name of the recipient or sender, e.g. 'Kanagarlasaiakhil' or 'Balaji')\n"
            "- payment_app (string: name of the app e.g. 'Amazon Pay', 'PhonePe', 'Google Pay', 'Paytm', 'CRED', 'SBI', 'Generic')\n"
            "- transaction_date (string: in YYYY-MM-DD format if date is found, or 'Today')\n"
            "- transaction_time (string: e.g. '7:54 PM' or '10:02 AM')\n"
            "- bank_name (string: name of bank involved, e.g. 'Statebankof India', 'HDFC Bank', 'ICICI Bank', or null)\n"
            "- reference_number (string: UTR or UPI Reference Number, 12 digits or alphanumeric string, or null)\n"
            "- raw_text (string: all readable text extracted from the receipt)\n"
            "- confidence (integer from 0 to 100 indicating extraction confidence)\n\n"
            f"Optional context caption from user: '{caption}'\n"
            "Return ONLY the JSON object, with no markdown code fences or backticks."
        )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        
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

        logger.info(f"Invoking Gemini Vision API ({GEMINI_MODEL}) for {image_path}...")
        response = requests.post(url, json=payload, timeout=25.0)

        if response.status_code != 200:
            logger.warning(f"Gemini API returned status {response.status_code}: {response.text}")
            return None, 0

        data = response.json()
        
        # Extract text from response candidates
        candidates = data.get("candidates", [])
        if not candidates:
            return None, 0

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return None, 0

        json_str = parts[0].get("text", "").strip()
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

        logger.info(f"Gemini Vision successfully parsed receipt: {tx_type} Rs. {amount} to/from {person} (Confidence: {confidence}%)")
        return transaction, confidence

    except Exception as e:
        logger.error(f"Error during Gemini Vision processing: {e}", exc_info=True)
        return None, 0
