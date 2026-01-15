# aiosyslogd/auth.py
# -*- coding: utf-8 -*-
import json
import os
from werkzeug.security import generate_password_hash, check_password_hash
from loguru import logger


class User:
    def __init__(
        self, username, password_hash, is_admin=False, is_enabled=True
    ):
        self.username = username
        self.password_hash = password_hash
        self.is_admin = is_admin
        self.is_enabled = is_enabled

    def to_dict(self):
        return {
            "username": self.username,
            "password_hash": self.password_hash,
            "is_admin": self.is_admin,
            "is_enabled": self.is_enabled,
        }

    @staticmethod
    def from_dict(data):
        return User(
            username=data.get("username"),
            password_hash=data.get("password_hash"),
            is_admin=data.get("is_admin", False),
            is_enabled=data.get("is_enabled", True),
        )


class AuthManager:
    def __init__(self, users_file):
        self.users_file = users_file
        self.users = self._load_users()

    def _load_users(self):
        if not os.path.exists(self.users_file):
            logger.info(
                f"Users file not found. Creating a default '{self.users_file}'..."
            )
            self._create_default_users_file()

        with open(self.users_file, "r") as f:
            try:
                users_data = json.load(f)
                return {
                    username: User.from_dict(data)
                    for username, data in users_data.items()
                }
            except json.JSONDecodeError:
                logger.error(
                    f"Error decoding JSON from {self.users_file}. Creating a new one."
                )
                self._create_default_users_file()
                with open(self.users_file, "r") as f:
                    users_data = json.load(f)
                    return {
                        username: User.from_dict(data)
                        for username, data in users_data.items()
                    }

    def _create_default_users_file(self):
        default_admin_password = "admin"
        default_admin_user = {
            "admin": User(
                username="admin",
                password_hash=generate_password_hash(default_admin_password),
                is_admin=True,
                is_enabled=True,
            ).to_dict()
        }
        with open(self.users_file, "w") as f:
            json.dump(default_admin_user, f, indent=4)
        logger.info(
            f"Default admin user created with password: {default_admin_password}"
        )

    def _save_users(self):
        with open(self.users_file, "w") as f:
            json.dump(
                {
                    username: user.to_dict()
                    for username, user in self.users.items()
                },
                f,
                indent=4,
            )

    def get_user(self, username):
        return self.users.get(username)

    def check_password(self, username, password):
        user = self.get_user(username)
        if user and user.is_enabled:
            return check_password_hash(user.password_hash, password)
        return False

    def add_user(self, username, password, is_admin=False):
        if username in self.users:
            return False, "User already exists."
        self.users[username] = User(
            username=username,
            password_hash=generate_password_hash(password),
            is_admin=is_admin,
            is_enabled=True,
        )
        self._save_users()
        return True, "User created successfully."

    def update_password(self, username, new_password):
        user = self.get_user(username)
        if not user:
            return False, "User not found."
        user.password_hash = generate_password_hash(new_password)
        self._save_users()
        return True, "Password updated successfully."

    def set_user_admin_status(self, username, is_admin):
        user = self.get_user(username)
        if not user:
            return False, "User not found."
        user.is_admin = is_admin
        self._save_users()
        return True, "User admin status updated successfully."

    def set_user_enabled_status(self, username, is_enabled):
        user = self.get_user(username)
        if not user:
            return False, "User not found."
        user.is_enabled = is_enabled
        self._save_users()
        return True, "User enabled status updated successfully."

    def delete_user(self, username):
        if username not in self.users:
            return False, "User not found."
        del self.users[username]
        self._save_users()
        return True, "User deleted successfully."
