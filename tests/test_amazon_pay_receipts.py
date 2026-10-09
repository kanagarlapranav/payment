"""
Comprehensive Tests for Amazon Pay Receipts & Multi-Modal Processing (Checklist Section 6):
1. Amazon Pay direct photo
2. Amazon Pay full screenshot
3. Amazon Pay Telegram image document
4. Missing MIME type
5. Supported extension with missing MIME
6. Unsupported extension
7. Low-resolution image
8. Gemini valid SENT response
9. Gemini valid RECEIVED response
10. Gemini invalid response
11. Gemini fallback to RapidOCR
12. Both Gemini and OCR fail
"""

import os
import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from PIL import Image
import httpx
import pytest

import ocr.gemini_vision as gv
from parsers.amazonpay import AmazonPayParser
from database.models import Transaction


@pytest.fixture(autouse=True)
def clean_test_environment(tmp_path, monkeypatch):
    """Provides a fresh isolated database for each test."""
    db_file = tmp_path / "test_amazon_pay.sqlite3"
    monkeypatch.setattr("config.DB_PATH", db_file)
    monkeypatch.setattr("database.db.DB_PATH", db_file)
    monkeypatch.setattr("services.backup_service.DB_PATH", db_file)
    from database.db import setup_database
    setup_database()
    return db_file


# 1. Amazon Pay direct photo
def test_amazon_pay_direct_photo_parser():
    raw_ocr = (
        "Amazon Pay\n"
        "Paid to Chai Point\n"
        "₹400.00\n"
        "UPI ID: chaipoint@apl\n"
        "Paid from: State Bank of India\n"
        "UPI Ref No: 123456789012\n"
        "15 Sep 2026, 07:54 PM"
    )
    parser = AmazonPayParser(raw_ocr)
    assert parser.can_parse() is True
    tx = parser.parse()
    assert tx is not None
    assert tx.amount == 400.0
    assert tx.transaction_type == "SENT"
    assert "Chai Point" in tx.person_name
    assert tx.reference_number == "123456789012"


# 2. Amazon Pay full screenshot (with chat noise and header)
def test_amazon_pay_full_screenshot_with_chat_noise():
    raw_ocr = (
        "Connecting...\n"
        "Amazon Pay\n"
        "Paid to Ramesh Stores\n"
        "₹1,250.00\n"
        "UPI Ref No: 987654321098\n"
        "Date: 20 Sep 2026 11:30 AM\n"
        "admin enter manually if needed"
    )
    parser = AmazonPayParser(raw_ocr)
    assert parser.can_parse() is True
    tx = parser.parse()
    assert tx is not None
    assert tx.amount == 1250.0
    assert tx.transaction_type == "SENT"
    assert "Ramesh Stores" in tx.person_name
    assert tx.reference_number == "987654321098"


# 3. Amazon Pay Telegram image document
def test_amazon_pay_telegram_image_document():
    from bot.handlers import handle_image
    update = MagicMock()
    update.callback_query = None
    msg = MagicMock()
    doc = MagicMock()
    doc.file_name = "amazon_receipt.png"
    doc.mime_type = "image/png"
    doc.file_size = 50000
    doc.file_id = "doc123"
    msg.photo = None
    msg.document = doc
    msg.caption = ""
    msg.reply_text = AsyncMock()
    update.message = msg
    update.effective_message = msg
    context = MagicMock()
    context.bot.get_file = AsyncMock()
    mock_file = AsyncMock()
    context.bot.get_file.return_value = mock_file

    async def _run():
        with patch("bot.handlers.require_admin", AsyncMock(return_value=True)), \
             patch("bot.handlers.deliver_response", AsyncMock()), \
             patch("bot.handlers.extract_transaction_with_gemini", return_value=(None, 0)), \
             patch("bot.handlers.perform_ocr_async", AsyncMock(return_value="")):
            await handle_image(update, context)
            msg.reply_text.assert_any_call("🔍 Reading receipt…")

    asyncio.run(_run())


# 4. Missing MIME type (None)
def test_document_missing_mime_type_handled_safely():
    from bot.handlers import handle_image
    update = MagicMock()
    update.callback_query = None
    msg = MagicMock()
    doc = MagicMock()
    doc.file_name = "receipt.jpg"
    doc.mime_type = None
    doc.file_size = 50000
    doc.file_id = "doc_no_mime"
    msg.photo = None
    msg.document = doc
    msg.caption = ""
    msg.reply_text = AsyncMock()
    update.message = msg
    update.effective_message = msg
    context = MagicMock()
    context.bot.get_file = AsyncMock()
    mock_file = AsyncMock()
    context.bot.get_file.return_value = mock_file

    async def _run():
        with patch("bot.handlers.require_admin", AsyncMock(return_value=True)), \
             patch("bot.handlers.deliver_response", AsyncMock()), \
             patch("bot.handlers.extract_transaction_with_gemini", return_value=(None, 0)), \
             patch("bot.handlers.perform_ocr_async", AsyncMock(return_value="")):
            await handle_image(update, context)
            msg.reply_text.assert_any_call("🔍 Reading receipt…")

    asyncio.run(_run())


# 5. Supported extension with missing MIME
def test_document_supported_extension_with_missing_mime():
    from bot.handlers import handle_image
    for ext_candidate in [".jpg", ".jpeg", ".png", ".webp"]:
        update = MagicMock()
        update.callback_query = None
        msg = MagicMock()
        doc = MagicMock()
        doc.file_name = f"scan{ext_candidate}"
        doc.mime_type = ""
        doc.file_size = 50000
        doc.file_id = f"doc_{ext_candidate}"
        msg.photo = None
        msg.document = doc
        msg.caption = ""
        msg.reply_text = AsyncMock()
        update.message = msg
        update.effective_message = msg
        context = MagicMock()
        context.bot.get_file = AsyncMock()
        mock_file = AsyncMock()
        context.bot.get_file.return_value = mock_file

        async def _run():
            with patch("bot.handlers.require_admin", AsyncMock(return_value=True)), \
                 patch("bot.handlers.deliver_response", AsyncMock()), \
                 patch("bot.handlers.extract_transaction_with_gemini", return_value=(None, 0)), \
                 patch("bot.handlers.perform_ocr_async", AsyncMock(return_value="")):
                await handle_image(update, context)
                msg.reply_text.assert_any_call("🔍 Reading receipt…")

        asyncio.run(_run())


# 6. Unsupported extension
def test_document_unsupported_extension_rejected():
    from bot.handlers import handle_image
    bad_files = ["statement.pdf", "receipt.txt", "animation.gif", "script.exe"]
    for bad in bad_files:
        update = MagicMock()
        update.callback_query = None
        msg = MagicMock()
        doc = MagicMock()
        doc.file_name = bad
        doc.mime_type = ""
        doc.file_size = 50000
        doc.file_id = "doc_bad"
        msg.photo = None
        msg.document = doc
        msg.caption = ""
        msg.reply_text = AsyncMock()
        update.message = msg
        update.effective_message = msg
        context = MagicMock()

        async def _run():
            with patch("bot.handlers.require_admin", AsyncMock(return_value=True)):
                await handle_image(update, context)
                msg.reply_text.assert_called_with("⚠️ Unsupported image format. Allowed formats: JPG, PNG, WEBP.")

        asyncio.run(_run())


# 7. Low-resolution image
def test_low_resolution_image_decode_and_b64(tmp_path):
    low_res_path = tmp_path / "low_res.jpg"
    img = Image.new("RGB", (64, 64), color="blue")
    img.save(low_res_path)

    b64, mime = gv._prepare_image_b64(str(low_res_path))
    assert mime == "image/jpeg"
    assert len(b64) > 0


# 8. Gemini valid SENT response
def test_amazon_pay_gemini_valid_sent_response():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "amount": 350.0,
                            "transaction_type": "SENT",
                            "person_name": "AMAZON SELLER",
                            "payment_app": "Amazon Pay",
                            "reference_number": "112233445566",
                            "transaction_date": "2026-09-21",
                            "transaction_time": "03:45 PM"
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
                        assert tx.amount == 350.0
                        assert tx.transaction_type == "SENT"
                        assert tx.person_name == "AMAZON SELLER"
                        assert tx.reference_number == "112233445566"

                        from services.transaction_service import commit_transaction
                        assert commit_transaction(tx) is True

                        from database.db import get_db_connection
                        with get_db_connection() as conn:
                            row = conn.cursor().execute(
                                "SELECT amount, transaction_type, person_name, reference_number FROM transactions WHERE reference_number = '112233445566'"
                            ).fetchone()
                            assert row is not None
                            assert row[0] == 350.0
                            assert row[1] == "SENT"
                            assert row[2] == "AMAZON SELLER"

    asyncio.run(_run())


# 9. Gemini valid RECEIVED response
def test_amazon_pay_gemini_valid_received_response():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "amount": 750.0,
                            "transaction_type": "RECEIVED",
                            "person_name": "CASHBACK REWARD",
                            "payment_app": "Amazon Pay",
                            "reference_number": "998877665544",
                            "transaction_date": "2026-09-21"
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
                        assert tx.amount == 750.0
                        assert tx.transaction_type == "RECEIVED"
                        assert tx.person_name == "CASHBACK REWARD"

                        from services.transaction_service import commit_transaction
                        assert commit_transaction(tx) is True

                        from database.db import get_db_connection
                        with get_db_connection() as conn:
                            row = conn.cursor().execute(
                                "SELECT amount, transaction_type, person_name, reference_number FROM transactions WHERE reference_number = '998877665544'"
                            ).fetchone()
                            assert row is not None
                            assert row[0] == 750.0
                            assert row[1] == "RECEIVED"
                            assert row[2] == "CASHBACK REWARD"

    asyncio.run(_run())


# 10. Gemini invalid response (rejected, never defaults to SENT)
def test_amazon_pay_gemini_invalid_response_rejected():
    bad_payloads = [
        {"amount": -50.0, "transaction_type": "SENT"},
        {"amount": 0.0, "transaction_type": "SENT"},
        {"amount": "NaN", "transaction_type": "SENT"},
        {"amount": 100.0, "transaction_type": "UNKNOWN"},
        {"amount": 100.0, "transaction_type": "INVALID_TYPE"},
        {"amount": None, "transaction_type": "SENT"}
    ]

    for bad in bad_payloads:
        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "candidates": [{
                    "content": {
                        "parts": [{"text": json.dumps(bad)}]
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
                            assert tx is None, f"Expected None for {bad}, got {tx}"
                            assert conf == 0

        asyncio.run(_run())


# 11. Gemini fallback to RapidOCR
def test_amazon_pay_gemini_fallback_to_rapidocr():
    # Simulate Gemini failure (returns None), RapidOCR fallback succeeds via process_transaction & commit_transaction
    raw_ocr = (
        "Amazon Pay\n"
        "Paid to Grocery Store\n"
        "₹500.00\n"
        "UPI Ref No: 123456789012\n"
        "15 Sep 2026, 07:54 PM"
    )
    with patch("ocr.gemini_vision.parse_text_with_gemini", side_effect=Exception("Gemini unavailable")):
        from services.transaction_service import process_transaction, commit_transaction
        tx, conf = process_transaction(raw_text=raw_ocr, image_path="dummy.jpg", message_id="101", chat_id="202")
        assert tx is not None
        assert tx.amount == 500.0
        assert tx.transaction_type == "SENT"
        assert "Grocery Store" in tx.person_name
        assert conf > 50

        assert commit_transaction(tx) is True

        from database.db import get_db_connection
        with get_db_connection() as conn:
            row = conn.cursor().execute(
                "SELECT amount, transaction_type, person_name, reference_number FROM transactions WHERE reference_number = '123456789012'"
            ).fetchone()
            assert row is not None
            assert row[0] == 500.0
            assert row[1] == "SENT"
            assert "Grocery Store" in row[2]


# 12. Both Gemini and OCR fail -> no transaction saved
def test_both_gemini_and_ocr_fail_saves_no_transaction():
    from database.db import get_db_connection
    with get_db_connection() as conn:
        count_before = conn.cursor().execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    from bot.handlers import handle_image
    update = MagicMock()
    update.callback_query = None
    msg = MagicMock()
    msg.photo = [MagicMock(file_size=5000, file_id="fid_fail")]
    msg.document = None
    msg.caption = ""
    msg.reply_text = AsyncMock()
    update.message = msg
    update.effective_message = msg
    context = MagicMock()
    context.bot.get_file = AsyncMock()
    mock_file = AsyncMock()
    context.bot.get_file.return_value = mock_file

    async def _run():
        with patch("bot.handlers.require_admin", AsyncMock(return_value=True)), \
             patch("bot.handlers.extract_transaction_with_gemini", return_value=(None, 0)), \
             patch("bot.handlers.perform_ocr_async", AsyncMock(return_value="")), \
             patch("bot.handlers.deliver_response", AsyncMock()) as mock_deliver:
            await handle_image(update, context)
            assert mock_deliver.called
            delivered_text = mock_deliver.call_args[0][2]
            assert "Could not read text from this image" in delivered_text or "Could not detect a valid amount" in delivered_text

    asyncio.run(_run())

    with get_db_connection() as conn:
        count_after = conn.cursor().execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert count_after == count_before
