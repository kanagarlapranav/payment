import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import ocr.gemini_vision as gv
from bot.commands import geministatus_command
from database.models import Transaction


class TestGeminiChatStatus(unittest.TestCase):
    def test_check_gemini_api_status_not_configured(self):
        with patch.object(gv, "get_effective_gemini_api_key", return_value=""):
            res = asyncio.run(gv.check_gemini_api_status_async())
            self.assertFalse(res["configured"])
            self.assertFalse(res["available"])
            self.assertEqual(res["status"], "NOT_CONFIGURED")

    def test_check_gemini_api_status_ok(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_resp

        with patch.object(gv, "get_effective_gemini_api_key", return_value="AIzaSy1234567890"):
            res = asyncio.run(gv.check_gemini_api_status_async(client=mock_client))
            self.assertTrue(res["configured"])
            self.assertTrue(res["available"])
            self.assertEqual(res["status"], "OK")
            self.assertEqual(res["http_code"], 200)
            self.assertIn("7890", res["masked_key"])

    def test_check_gemini_api_status_quota_exceeded(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 429
        mock_resp.json.return_value = {
            "error": {
                "code": 429,
                "message": "Quota exceeded for metric generativelanguage.googleapis.com/generate_content_free_tier_requests",
                "status": "RESOURCE_EXHAUSTED",
            }
        }
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_resp

        with patch.object(gv, "get_effective_gemini_api_key", return_value="AIzaSy1234567890"):
            res = asyncio.run(gv.check_gemini_api_status_async(client=mock_client))
            self.assertTrue(res["configured"])
            self.assertFalse(res["available"])
            self.assertEqual(res["status"], "QUOTA_EXCEEDED")
            self.assertEqual(res["http_code"], 429)
            self.assertEqual(res["daily_limit"], 20)
            self.assertIn("Quota exceeded", res["message"])

    def test_check_gemini_api_status_credential_error(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 403
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_resp

        with patch.object(gv, "get_effective_gemini_api_key", return_value="AIzaSy1234567890"):
            res = asyncio.run(gv.check_gemini_api_status_async(client=mock_client))
            self.assertTrue(res["configured"])
            self.assertFalse(res["available"])
            self.assertEqual(res["status"], "CREDENTIAL_ERROR")
            self.assertEqual(res["http_code"], 403)

    def test_geministatus_command_outputs_quota_card(self):
        update = MagicMock()
        update.effective_user.id = 123456
        update.message = AsyncMock()
        status_msg = AsyncMock()
        update.message.reply_text.return_value = status_msg
        context = MagicMock()

        mock_status = {
            "configured": True,
            "available": False,
            "status": "QUOTA_EXCEEDED",
            "http_code": 429,
            "model": "gemini-flash-latest",
            "message": "Daily quota exceeded",
            "masked_key": "…7890",
            "daily_limit": 20,
        }

        with patch("bot.auth.require_authorized", AsyncMock(return_value=True)), \
             patch("ocr.gemini_vision.check_gemini_api_status_async", AsyncMock(return_value=mock_status)):
            asyncio.run(geministatus_command(update, context))

            status_msg.edit_text.assert_called_once()
            call_text = status_msg.edit_text.call_args[0][0]
            self.assertIn("Daily Quota Exceeded", call_text)
            self.assertIn("429 Resource Exhausted", call_text)
            self.assertIn("RapidOCR", call_text)

    def test_image_handler_displays_quota_warning_when_ocr_fails(self):
        from bot.handlers import handle_image

        photo_mock = MagicMock()
        photo_mock.file_size = 50000
        photo_mock.file_id = "test_fid"

        update = MagicMock()
        update.message = AsyncMock()
        update.message.photo = [photo_mock]
        update.message.caption = ""
        update.message.chat_id = 123456
        update.message.message_id = 999
        status_msg = AsyncMock()
        update.message.reply_text.return_value = status_msg
        context = MagicMock()
        context.bot.get_file = AsyncMock()
        mock_file = AsyncMock()
        context.bot.get_file.return_value = mock_file

        async def _run():
            with patch("bot.handlers.require_admin", AsyncMock(return_value=True)), \
                 patch("bot.handlers.is_gemini_available", return_value=True), \
                 patch("ocr.gemini_vision.get_last_extraction_error", return_value="RATE_LIMIT"), \
                 patch("bot.handlers.extract_transaction_with_gemini", return_value=(None, 0)), \
                 patch("bot.handlers.perform_ocr_async", AsyncMock(return_value="")), \
                 patch("bot.handlers.deliver_response", AsyncMock()) as mock_deliver:
                await handle_image(update, context)
                self.assertTrue(mock_deliver.called)
                delivered_text = mock_deliver.call_args[0][2]
                self.assertIn("Gemini Vision daily quota exceeded for today (429 Rate Limit)", delivered_text)

        asyncio.run(_run())

    def test_image_handler_displays_notice_when_rapidocr_recovers(self):
        from bot.handlers import handle_image

        photo_mock = MagicMock()
        photo_mock.file_size = 50000
        photo_mock.file_id = "test_fid"

        update = MagicMock()
        update.message = AsyncMock()
        update.message.photo = [photo_mock]
        update.message.caption = ""
        update.message.chat_id = 123456
        update.message.message_id = 999
        status_msg = AsyncMock()
        update.message.reply_text.return_value = status_msg
        context = MagicMock()
        context.bot.get_file = AsyncMock()
        mock_file = AsyncMock()
        context.bot.get_file.return_value = mock_file

        recovered_tx = Transaction(
            amount=450.0,
            transaction_type="SENT",
            person_name="Sample Merchant",
            payment_app="Google Pay",
        )

        async def _run():
            with patch("bot.handlers.require_admin", AsyncMock(return_value=True)), \
                 patch("bot.handlers.is_gemini_available", return_value=True), \
                 patch("ocr.gemini_vision.get_last_extraction_error", return_value="RATE_LIMIT"), \
                 patch("bot.handlers.extract_transaction_with_gemini", return_value=(None, 0)), \
                 patch("bot.handlers.perform_ocr_async", AsyncMock(return_value="Paid 450 to Sample Merchant")), \
                 patch("bot.handlers.process_transaction", return_value=(recovered_tx, 85)), \
                 patch("bot.handlers.find_potential_duplicate", return_value=None), \
                 patch("bot.handlers.deliver_response", AsyncMock()) as mock_deliver:
                await handle_image(update, context)
                self.assertTrue(mock_deliver.called)
                card_delivered = mock_deliver.call_args[0][2]
                self.assertIn("Extracted with local RapidOCR (Gemini daily quota reached)", card_delivered)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
