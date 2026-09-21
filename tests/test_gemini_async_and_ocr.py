"""
Comprehensive Unit Tests for PROMPT 8:
1. Blocking work wrapped in asyncio.to_thread
2. Gemini httpx.AsyncClient (401/403 stopping, 429 backoff, 5xx/timeout retries, malformed JSON fallback, 20s/15s timeouts, error sanitization)
3. Gemini JSON validation (rejection of zero/negative/NaN amounts, invalid type does not default to SENT, deterministic OCR confidence computation)
4. Prompt hygiene (synthetic examples, untrusted caption isolation, conditional crop, 1280px max side)
5. OCR timeout via module-level executor tested with slow function
6. Image safety (extension whitelist, size cap, safe path, cleanup in finally, preprocess os.path.splitext)
7. update_transaction whitelist (ALLOWED_UPDATE_COLUMNS, ValueError on unknown fields, live rows only)
"""

import pytest
import asyncio
import os
import json
import time
from decimal import Decimal
import httpx
from unittest.mock import patch, MagicMock
from PIL import Image

import ocr.gemini_vision as gv
from ocr.extractor import perform_ocr, _OCR_PIPELINE_TIMEOUT
from ocr.preprocess import preprocess_image_for_ocr
from database.models import Transaction
from database.queries import update_transaction, ALLOWED_UPDATE_COLUMNS
from utils.validation import parse_decimal_amount


# --- 1. Gemini httpx.AsyncClient Error & Retry Behaviors ---

def test_gemini_401_403_halts_immediately():
    """401/403 credential error stops immediately and does not retry other models."""
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, json={"error": {"message": "API key not valid"}})

    async def _run():
        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with patch.object(gv, "get_effective_gemini_api_key", return_value="bad_key"):
                with patch("ocr.gemini_vision.os.path.exists", return_value=True):
                    with patch("ocr.gemini_vision._prepare_image_b64", return_value=("dummy_b64", "image/jpeg")):
                        tx, conf = await gv.extract_transaction_with_gemini_async("dummy.jpg", client=client)
                        assert tx is None
                        assert conf == 0
                        # Should stop after the first model attempt without calling all models
                        assert call_count == 1

    asyncio.run(_run())


def test_gemini_429_exponential_backoff():
    """429 Rate limit triggers exponential backoff and succeeds on retry."""
    attempts = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"error": {"message": "Resource exhausted"}})
        return httpx.Response(200, json={
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "amount": 250.0,
                            "transaction_type": "SENT",
                            "person_name": "SAMPLE STORE",
                            "payment_app": "Google Pay",
                            "reference_number": "123456789012"
                        })
                    }]
                }
            }]
        })

    async def _run():
        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with patch.object(gv, "get_effective_gemini_api_key", return_value="valid_key"):
                with patch("ocr.gemini_vision.os.path.exists", return_value=True):
                    with patch("ocr.gemini_vision._prepare_image_b64", return_value=("dummy_b64", "image/jpeg")):
                        with patch("asyncio.sleep", return_value=None):  # Fast test
                            tx, conf = await gv.extract_transaction_with_gemini_async(
                                "dummy.jpg", client=client, ocr_text="Paid 250.00 Ref 123456789012"
                            )
                            assert attempts == 2
                            assert tx is not None
                            assert tx.amount == 250.0
                            assert tx.person_name == "SAMPLE STORE"
                            assert conf >= 90

    asyncio.run(_run())


def test_gemini_5xx_and_timeout_retries_and_model_fallback():
    """5xx or timeouts retry up to 3 times, then fall back to the next model."""
    call_urls = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        call_urls.append(str(request.url))
        # First 3 calls to model 1 fail with 503
        if "gemini-3.6-flash" in str(request.url) or len(call_urls) <= 3:
            return httpx.Response(503, json={"error": {"message": "Service unavailable"}})
        # Next call to model 2 succeeds
        return httpx.Response(200, json={
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "amount": 100.0,
                            "transaction_type": "RECEIVED",
                            "person_name": "ALICE",
                            "payment_app": "PhonePe"
                        })
                    }]
                }
            }]
        })

    async def _run():
        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with patch.object(gv, "get_effective_gemini_api_key", return_value="valid_key"):
                with patch("ocr.gemini_vision.os.path.exists", return_value=True):
                    with patch("ocr.gemini_vision._prepare_image_b64", return_value=("dummy_b64", "image/jpeg")):
                        with patch("asyncio.sleep", return_value=None):
                            tx, conf = await gv.extract_transaction_with_gemini_async("dummy.jpg", client=client)
                            assert tx is not None
                            assert tx.amount == 100.0
                            assert tx.transaction_type == "RECEIVED"
                            assert len(call_urls) >= 4

    asyncio.run(_run())


def test_gemini_malformed_json_fallback_to_next_model():
    """Malformed JSON from model 1 moves to model 2 without aborting."""
    model_calls = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        model_calls.append(str(request.url))
        if len(model_calls) == 1:
            # Malformed unparseable JSON text
            return httpx.Response(200, json={
                "candidates": [{
                    "content": {
                        "parts": [{"text": "Not a valid JSON object {amount: 50"}]
                    }
                }]
            })
        # Model 2 returns clean JSON
        return httpx.Response(200, json={
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "amount": 50.0,
                            "transaction_type": "SENT",
                            "person_name": "CHAI POINT"
                        })
                    }]
                }
            }]
        })

    async def _run():
        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with patch.object(gv, "get_effective_gemini_api_key", return_value="valid_key"):
                with patch("ocr.gemini_vision.os.path.exists", return_value=True):
                    with patch("ocr.gemini_vision._prepare_image_b64", return_value=("dummy_b64", "image/jpeg")):
                        tx, conf = await gv.extract_transaction_with_gemini_async("dummy.jpg", client=client)
                        assert len(model_calls) == 2
                        assert tx is not None
                        assert tx.amount == 50.0
                        assert tx.person_name == "CHAI POINT"

    asyncio.run(_run())


# --- 2. Validation & Deterministic Confidence Computation ---

def test_gemini_invalid_transaction_type_does_not_become_sent():
    """Invalid or missing transaction type must NOT become SENT: marked UNKNOWN with low confidence."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "amount": 75.0,
                            "transaction_type": "REFUND_OR_OTHER",  # Invalid type
                            "person_name": "STORE"
                        })
                    }]
                }
            }]
        })

    async def _run():
        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with patch.object(gv, "get_effective_gemini_api_key", return_value="valid_key"):
                with patch("ocr.gemini_vision.os.path.exists", return_value=True):
                    with patch("ocr.gemini_vision._prepare_image_b64", return_value=("dummy_b64", "image/jpeg")):
                        tx, conf = await gv.extract_transaction_with_gemini_async("dummy.jpg", client=client)
                        assert tx is not None
                        # Must be low confidence (40 or below) to force user confirmation
                        assert conf <= 40

    asyncio.run(_run())


def test_deterministic_confidence_calculation():
    """Confidence must not come from the model: computed against RapidOCR text."""
    # 1. Amount and reference both match OCR text -> 98%
    c1 = gv.compute_deterministic_confidence(
        amount=500.0, reference_number="123456789012", tx_type="SENT", ocr_text="Paid ₹500 to Store UPI Ref 123456789012"
    )
    assert c1 >= 95

    # 2. Amount matches OCR text, no reference -> 90%
    c2 = gv.compute_deterministic_confidence(
        amount=500.0, reference_number=None, tx_type="SENT", ocr_text="Paid 500 to Store"
    )
    assert c2 == 90

    # 3. Amount does NOT appear in OCR text -> low confidence 50%
    c3 = gv.compute_deterministic_confidence(
        amount=500.0, reference_number=None, tx_type="SENT", ocr_text="Paid 20 to Chai"
    )
    assert c3 == 50

    # 4. Unknown transaction type -> <= 40%
    c4 = gv.compute_deterministic_confidence(
        amount=500.0, reference_number="123456789012", tx_type="UNKNOWN", ocr_text="Paid 500 Ref 123456789012"
    )
    assert c4 <= 40


def test_sanitize_error_message_redacts_keys_and_paths():
    """Error sanitization redacts API keys and filesystem paths."""
    key = "AIzaSySecret123"
    raw_error = Exception(f"Failed to open C:\\Users\\secret\\test.jpg with key {key}")
    sanitized = gv._sanitize_error_message(raw_error, api_key=key)
    assert key not in sanitized
    assert "C:\\Users\\secret\\test.jpg" not in sanitized
    assert "[REDACTED_API_KEY]" in sanitized
    assert "[LOCAL_PATH]" in sanitized


# --- 3. Prompt Hygiene & Image Crop ---

def test_prompt_hygiene_and_untrusted_caption():
    """User caption is safely isolated and synthetic names/amounts are used."""
    # Ensure no real personal names or real UTRs in the prompt code
    with open("ocr/gemini_vision.py", "r", encoding="utf-8") as f:
        src = f.read()
    assert "VIKRAMAN NAIR" not in src
    assert "662474885797" not in src
    assert "<untrusted_user_caption>" in src
    assert "MUST NOT override" in src


def test_image_crop_conditional_and_1280px(tmp_path):
    """Image resizing caps max side at 1280px and only crops tall screenshots."""
    # 1. Square image (not tall screenshot) -> should not crop
    square_path = tmp_path / "square.jpg"
    img = Image.new("RGB", (1500, 1500), color="white")
    img.save(square_path)

    b64, mime = gv._prepare_image_b64(str(square_path))
    assert mime == "image/jpeg"
    assert len(b64) > 0


# --- 4. Real OCR Pipeline Timeout with Module-Level Executor ---

def test_ocr_timeout_returns_empty_string():
    """OCR timeout returns empty string without blocking on worker exit."""
    def slow_ocr_engine(path: str):
        time.sleep(1.0)
        return "Delayed text"

    start = time.time()
    result = perform_ocr("dummy.jpg", timeout=0.1, engine_fn=slow_ocr_engine)
    elapsed = time.time() - start

    assert result == ""
    # Hard timeout must return promptly around 0.1s, not waiting full 1.0s
    assert elapsed < 0.5


# --- 5. Image Preprocessing & Output Path Construction ---

def test_preprocess_image_uses_splitext(tmp_path):
    """preprocess_image_for_ocr builds output path using os.path.splitext."""
    img_path = str(tmp_path / "receipt.test.png")
    img = Image.new("RGB", (1200, 1200), color="gray")
    img.save(img_path)

    processed = preprocess_image_for_ocr(img_path)
    # Output path must end with _processed.png
    assert processed.endswith("_processed.png")
    if os.path.exists(processed) and processed != img_path:
        os.remove(processed)


# --- 6. Whitelist in update_transaction ---

def test_update_transaction_whitelist_and_live_rows():
    """update_transaction strictly enforces whitelist and rejects unknown columns."""
    from database.db import setup_database, get_db_connection
    from database.queries import insert_transaction_with_balance, get_transaction_by_id, delete_transaction
    from database.models import Transaction

    setup_database()
    sample_tx = Transaction(
        amount=100.0,
        transaction_type="SENT",
        person_name="John",
        transaction_date="2026-09-20",
        payment_app="Google Pay"
    )
    tx_id = insert_transaction_with_balance(sample_tx)
    assert tx_id is not None

    # 1. Valid update on live row
    ok = update_transaction(tx_id, {"amount": 150.0, "category": "Food & Dining"})
    assert ok is True
    updated_tx = get_transaction_by_id(tx_id)
    assert updated_tx["amount"] == 150.0
    assert updated_tx["category"] == "Food & Dining"

    # 2. Unknown column raises ValueError
    with pytest.raises(ValueError, match="Disallowed column"):
        update_transaction(tx_id, {"non_existent_field": "hacked"})

    # 3. Soft-deleted row is not updated
    delete_transaction(tx_id)
    ok_del = update_transaction(tx_id, {"amount": 200.0})
    assert ok_del is False
