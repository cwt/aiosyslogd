import os
import pytest
from unittest.mock import patch
from aiosyslogd.auth import AuthManager, User


@pytest.fixture
def auth_manager(tmp_path):
    users_file = tmp_path / "users.json"
    return AuthManager(str(users_file))


def test_load_users_creates_default_if_missing(tmp_path):
    users_file = tmp_path / "users.json"
    assert not os.path.exists(users_file)

    manager = AuthManager(str(users_file))

    assert os.path.exists(users_file)
    assert "admin" in manager.users
    assert manager.users["admin"].is_admin
    assert manager.check_password("admin", "admin")


def test_load_users_handles_json_error(tmp_path):
    users_file = tmp_path / "users.json"
    with open(users_file, "w") as f:
        f.write("invalid json")

    # Should log error and recreate file
    with patch("aiosyslogd.auth.logger.error") as mock_log:
        manager = AuthManager(str(users_file))
        mock_log.assert_called()

    assert "admin" in manager.users
    assert manager.check_password("admin", "admin")


def test_add_user(auth_manager):
    success, msg = auth_manager.add_user("testuser", "password123")
    assert success
    assert "testuser" in auth_manager.users
    assert auth_manager.check_password("testuser", "password123")

    # Try adding again
    success, msg = auth_manager.add_user("testuser", "newpass")
    assert not success
    assert "User already exists" in msg


def test_update_password(auth_manager):
    auth_manager.add_user("testuser", "oldpass")

    success, msg = auth_manager.update_password("testuser", "newpass")
    assert success
    assert auth_manager.check_password("testuser", "newpass")
    assert not auth_manager.check_password("testuser", "oldpass")

    success, msg = auth_manager.update_password("nonexistent", "pass")
    assert not success
    assert "User not found" in msg


def test_set_user_admin_status(auth_manager):
    auth_manager.add_user("testuser", "pass", is_admin=False)
    assert not auth_manager.users["testuser"].is_admin

    success, msg = auth_manager.set_user_admin_status("testuser", True)
    assert success
    assert auth_manager.users["testuser"].is_admin

    success, msg = auth_manager.set_user_admin_status("nonexistent", True)
    assert not success


def test_set_user_enabled_status(auth_manager):
    auth_manager.add_user("testuser", "pass")
    assert auth_manager.users["testuser"].is_enabled

    success, msg = auth_manager.set_user_enabled_status("testuser", False)
    assert success
    assert not auth_manager.users["testuser"].is_enabled

    # Check password should fail if disabled
    assert not auth_manager.check_password("testuser", "pass")

    success, msg = auth_manager.set_user_enabled_status("nonexistent", True)
    assert not success


def test_delete_user(auth_manager):
    auth_manager.add_user("testuser", "pass")

    success, msg = auth_manager.delete_user("testuser")
    assert success
    assert "testuser" not in auth_manager.users

    success, msg = auth_manager.delete_user("testuser")
    assert not success
    assert "User not found" in msg


def test_check_password_nonexistent(auth_manager):
    assert not auth_manager.check_password("nobody", "pass")


def test_user_to_from_dict():
    user = User("u", "hash", is_admin=True, is_enabled=False)
    d = user.to_dict()
    assert d["username"] == "u"
    assert d["password_hash"] == "hash"
    assert d["is_admin"] is True
    assert d["is_enabled"] is False

    u2 = User.from_dict(d)
    assert u2.username == "u"
    assert u2.password_hash == "hash"
    assert u2.is_admin is True
    assert u2.is_enabled is False
