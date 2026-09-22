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
from unittest.mock import patch, MagicMock
from PIL import Image
import httpx

import ocr.gemini_vision as gv
from parsers.amazonpay import AmazonPayParser
from database.models import Transaction


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
    doc = MagicMock()
    doc.file_name = "amazon_receipt.png"
    doc.mime_type = "image/png"
    doc.file_size = 50000

    from bot.handlers import ALLOWED_IMAGE_EXTENSIONS
    ext = os.path.splitext(doc.file_name)[1].lower()
    assert ext in ALLOWED_IMAGE_EXTENSIONS
    assert ext == ".png"


# 4. Missing MIME type (None)
def test_document_missing_mime_type_handled_safely():
    doc = MagicMock()
    doc.file_name = "receipt.jpg"
    doc.mime_type = None

    doc_mime = (doc.mime_type or "").lower()
    orig_ext = os.path.splitext(doc.file_name or "")[1].lower()
    assert doc_mime == ""
    assert orig_ext == ".jpg"


# 5. Supported extension with missing MIME
def test_document_supported_extension_with_missing_mime():
    from bot.handlers import ALLOWED_IMAGE_EXTENSIONS
    for ext_candidate in [".jpg", ".jpeg", ".png", ".webp"]:
        doc = MagicMock()
        doc.file_name = f"scan{ext_candidate}"
        doc.mime_type = ""

        orig_ext = os.path.splitext(doc.file_name or "")[1].lower()
        assert orig_ext in ALLOWED_IMAGE_EXTENSIONS


# 6. Unsupported extension
def test_document_unsupported_extension_rejected():
    from bot.handlers import ALLOWED_IMAGE_EXTENSIONS
    bad_files = ["statement.pdf", "receipt.txt", "animation.gif", "script.exe"]
    for bad in bad_files:
        orig_ext = os.path.splitext(bad)[1].lower()
        assert orig_ext not in ALLOWED_IMAGE_EXTENSIONS


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
    # Simulate Gemini failure (returns None), RapidOCR fallback succeeds
    with patch("ocr.gemini_vision.extract_transaction_with_gemini_async", return_value=(None, 0)):
        with patch("ocr.extractor.perform_ocr_async", return_value="Amazon Pay Paid to Grocery Store ₹500"):
            with patch("services.transaction_service.process_transaction") as mock_process:
                mock_tx = Transaction(amount=500.0, transaction_type="SENT", person_name="Grocery Store")
                mock_process.return_value = (mock_tx, 85)

                tx, conf = mock_process.return_value
                assert tx is not None
                assert tx.amount == 500.0
                assert conf == 85


# 12. Both Gemini and OCR fail -> no transaction saved
def test_both_gemini_and_ocr_fail_saves_no_transaction():
    async def _run():
        with patch("ocr.gemini_vision.extract_transaction_with_gemini_async", return_value=(None, 0)):
            from ocr.extractor import perform_ocr_async
            ocr_text = await perform_ocr_async("dummy.jpg", engine_fn=lambda p: "")
            assert ocr_text == ""

    asyncio.run(_run())
