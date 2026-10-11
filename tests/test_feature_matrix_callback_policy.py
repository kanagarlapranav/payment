import os
import re
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from bot.auth import CALLBACK_ROLE_POLICY, WORKSPACE_CALLBACK_POLICY
from bot.handlers import _dispatch_callback_query


def test_perm_matrix_callbacks_reach_handlers_for_owner():
    """Verify owner can dispatch perm_matrix callbacks without getting 'no longer active'."""
    actions = [
        "perm_matrix_menu",
        "perm_mat_view:export",
        "perm_mat_toggle:export:member",
        "perm_mat_reset:export",
        "perm_mat_reset_all"
    ]

    async def _run():
        for data in actions:
            update = MagicMock()
            update.callback_query.data = data
            update.callback_query.answer = AsyncMock()
            update.effective_user.id = 12345
            context = MagicMock()

            # Global owner
            with patch("bot.auth.is_global_owner", return_value=True), \
                 patch("bot.auth.require_owner", AsyncMock(return_value=True)), \
                 patch("bot.handlers.safe_edit_callback_message", AsyncMock()):
                await _dispatch_callback_query(update, context)

            # Assert it did NOT answer with the 'no longer active' rejection
            for call in update.callback_query.answer.call_args_list:
                args, kwargs = call
                msg = args[0] if args else kwargs.get("text", "")
                assert "no longer active" not in msg, f"Action '{data}' got rejected as inactive!"

    asyncio.run(_run())


def test_forged_perm_mat_toggle_from_non_owner_denied():
    """Verify non-owner dispatching perm_mat_toggle is denied, not routed."""
    async def _run():
        update = MagicMock()
        update.callback_query.data = "perm_mat_toggle:export:member"
        update.callback_query.answer = AsyncMock()
        update.effective_user.id = 99999
        context = MagicMock()

        with patch("bot.auth.is_global_owner", return_value=False), \
             patch("bot.auth.require_owner", AsyncMock(return_value=False)), \
             patch("bot.handlers.safe_edit_callback_message", AsyncMock()) as mock_edit:
            await _dispatch_callback_query(update, context)
            # Should not edit message to render panel
            mock_edit.assert_not_called()

    asyncio.run(_run())


def test_all_callback_data_literals_registered_in_policy():
    """
    Prevention Rule:
    Collects every inline keyboard callback_data= literal in bot/
    and asserts each action prefix exists in CALLBACK_ROLE_POLICY.
    """
    bot_dir = Path("bot")
    cb_pattern = re.compile(r'callback_data=["\']([^"\']+)["\']')
    
    found_prefixes = set()
    for root, _, files in os.walk(bot_dir):
        for f in files:
            if f.endswith(".py"):
                path = Path(root) / f
                content = path.read_text(encoding="utf-8")
                matches = cb_pattern.findall(content)
                for m in matches:
                    prefix = m.split(":")[0]
                    # Skip format string templates / placeholders if any
                    if "{" not in prefix and prefix.strip():
                        found_prefixes.add(prefix.strip())

    unregistered = []
    for prefix in sorted(found_prefixes):
        if prefix not in CALLBACK_ROLE_POLICY and prefix not in WORKSPACE_CALLBACK_POLICY:
            unregistered.append(prefix)

    assert not unregistered, f"Unregistered callback actions found in UI keyboards: {unregistered}"
