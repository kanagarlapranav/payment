import sys; sys.path.insert(0, '.')
from database.queries import set_user_permission_and_role, get_workspace_member, get_default_workspace_id, get_all_users_for_permissions

ws_id = get_default_workspace_id()

# Test: set Nagendra to admin
result = set_user_permission_and_role(8343764796, 'admin', is_active=True)
m = get_workspace_member(ws_id, 8343764796)
print("Nagendra role after admin set:", m.role if m else "NOT FOUND")

# Test: check all users
users = get_all_users_for_permissions()
print("ALL USERS:")
for u in users:
    uid = u["telegram_user_id"]
    name = u["display_name"]
    role = u["role"]
    active = u["is_active"]
    print(f"  uid={uid} name={name} role={role} active={active}")

# Revert
set_user_permission_and_role(8343764796, 'member', is_active=True)
m2 = get_workspace_member(ws_id, 8343764796)
print("Nagendra after revert:", m2.role if m2 else "NOT FOUND")
