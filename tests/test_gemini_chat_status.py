import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import ocr.gemini_vision as gv
from bot.commands import geministatus_command
from database.models import Transaction


class TestGeminiChatStatus(unittest.TestCase):
    def setUp(self):
        gv.clear_status_cache()

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

    def test_check_gemini_api_status_cache(self):
        gv.clear_status_cache()
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200

        with patch.object(gv, "get_effective_gemini_api_key", return_value="AIzaSy1234567890"), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp

            # 1st call: performs network call
            res1 = asyncio.run(gv.check_gemini_api_status_async())
            self.assertEqual(res1["status"], "OK")
            initial_call_count = mock_post.call_count
            self.assertGreater(initial_call_count, 0)

            # 2nd call with force_refresh=False: uses cache, no additional network call
            res2 = asyncio.run(gv.check_gemini_api_status_async(force_refresh=False))
            self.assertEqual(res2["status"], "OK")
            self.assertEqual(mock_post.call_count, initial_call_count)

            # 3rd call with force_refresh=True: bypasses cache and re-probes
            res3 = asyncio.run(gv.check_gemini_api_status_async(force_refresh=True))
            self.assertEqual(res3["status"], "OK")
            self.assertGreater(mock_post.call_count, initial_call_count)

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

    def test_default_model_priority_order(self):
        with patch("database.queries.get_model_setting", return_value="AUTO"), \
             patch("ocr.gemini_vision.GEMINI_MODEL", ""):
            models = gv.get_effective_model_list()
            self.assertEqual(models[0], "gemini-3.8-flash")
            self.assertEqual(models[1], "gemini-3.7-flash")
            self.assertEqual(models[2], "gemini-3.6-flash")
            self.assertEqual(models[3], "gemini-3.5-flash-lite")

    def test_manual_model_priority_override(self):
        with patch("database.queries.get_model_setting", return_value="gemini-3.6-flash"):
            models = gv.get_effective_model_list()
            self.assertEqual(models[0], "gemini-3.6-flash")
            self.assertIn("gemini-3.8-flash", models)
            self.assertIn("gemini-3.7-flash", models)
            self.assertIn("gemini-3.5-flash-lite", models)

    def test_set_and_get_model_setting_database(self):
        from database.queries import set_model_setting, get_model_setting
        set_model_setting("gemini-3.7-flash")
        self.assertEqual(get_model_setting(), "gemini-3.7-flash")
        set_model_setting("AUTO")
        self.assertEqual(get_model_setting(), "AUTO")

    def test_setmodel_command_admin_updates_setting(self):
        from bot.commands import setmodel_command
        from database.queries import get_model_setting

        update = MagicMock()
        update.effective_user.id = 123456
        update.message = AsyncMock()
        context = MagicMock()
        context.args = ["3.8"]

        with patch("bot.auth.require_admin", AsyncMock(return_value=True)):
            asyncio.run(setmodel_command(update, context))
            self.assertEqual(get_model_setting(), "gemini-3.8-flash")
            update.message.reply_text.assert_called_once()
            call_text = update.message.reply_text.call_args[0][0]
            self.assertIn("Gemini Model Preference Updated", call_text)
            self.assertIn("gemini-3.8-flash", call_text)

    def test_set_model_callback_updates_setting_and_renders(self):
        from bot.handlers import handle_callback_query
        from database.queries import get_model_setting

        update = MagicMock()
        query = AsyncMock()
        query.data = "set_model:gemini-3.5-flash-lite"
        update.callback_query = query

        with patch("bot.handlers.require_admin", AsyncMock(return_value=True)), \
             patch("ocr.gemini_vision.check_gemini_api_status_async", AsyncMock(return_value={
                 "status": "OK", "model": "gemini-3.5-flash-lite", "masked_key": "…1234", "preferred_setting": "gemini-3.5-flash-lite"
             })):
            asyncio.run(handle_callback_query(update, MagicMock()))
            self.assertEqual(get_model_setting(), "gemini-3.5-flash-lite")
            query.answer.assert_called()
            query.edit_message_text.assert_called_once()


if __name__ == "__main__":
    unittest.main()

