"""
Simulate what Pranav (bot owner) sees when using /history and /permissions from a private chat.
Tests the exact auth flows that are failing.
"""
import sys; sys.path.insert(0, '.')
import config

print("="*60)
print("TEST 1: Bot owner sees transactions via /history (private DM)")
print("="*60)
from database.queries import get_transactions_paginated, get_default_workspace_id
ws_id = get_default_workspace_id()
print(f"Default workspace: {ws_id}")
data = get_transactions_paginated(page=1, page_size=5, workspace_id=ws_id)
print(f"Total transactions found: {data['total_count']}")
print(f"Pages: {data['total_pages']}")
for t in data['transactions']:
    print(f"  #{t['id']} {t['transaction_type']} Rs{t['amount']} {t['person_name']} on {t['transaction_date']}")

print()
print("="*60)
print("TEST 2: is_owner check for Pranav vs Nagendra")
print("="*60)
# We can't test with real Update objects offline, but check DB logic
from database.queries import get_workspace_member
m_pranav = get_workspace_member(ws_id, 8379948573)
m_nagendra = get_workspace_member(ws_id, 8343764796)
print(f"Pranav in Payment Group: role={m_pranav.role if m_pranav else 'NOT FOUND'}, active={m_pranav.is_active if m_pranav else 'N/A'}")
print(f"Nagendra in Payment Group: role={m_nagendra.role if m_nagendra else 'NOT FOUND'}, active={m_nagendra.is_active if m_nagendra else 'N/A'}")

print()
print("="*60)
print("TEST 3: Simulate perm_set admin for Nagendra")
print("="*60)
from database.queries import set_user_permission_and_role, get_all_users_for_permissions
# Try setting admin
result = set_user_permission_and_role(8343764796, 'admin', is_active=True)
print(f"set_user_permission_and_role returned: {result}")
# Check result
m_nag_after = get_workspace_member(ws_id, 8343764796)
print(f"Nagendra role after admin set: {m_nag_after.role if m_nag_after else 'NOT FOUND'}")

print()
print("="*60)
print("TEST 4: render_user_permission_card for Nagendra")
print("="*60)
from bot.commands import render_user_permission_card
card, markup = render_user_permission_card(8343764796)
print(card[:500])
print("Buttons:")
for row in markup.inline_keyboard:
    for btn in row:
        print(f"  [{btn.text}] => {btn.callback_data}")

print()
print("="*60)
print("TEST 5: get_all_users_for_permissions")
print("="*60)
users = get_all_users_for_permissions()
print(f"Total users visible: {len(users)}")
for u in users:
    print(f"  uid={u['telegram_user_id']} name={u['display_name']} role={u['role']} active={u['is_active']}")

# Reset nagendra back to member so tests don't affect production
set_user_permission_and_role(8343764796, 'member', is_active=True)
print()
print("Nagendra reset back to member.")
