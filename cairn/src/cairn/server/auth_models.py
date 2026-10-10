"""Pydantic models for authentication requests and responses.

This module is intentionally a standalone module (``auth_models.py``) rather than
a ``models/auth.py`` package member. The existing ``cairn.server.models`` is a
single module (``models.py``) that is imported across the dispatcher and server
(``from cairn.server.models import ...``). Introducing a ``models/`` package would
shadow that module and break those imports, so these auth models live in their own
additive module instead.

Validation rules (see requirements 1.4, 1.6, 1.7, 4.1, 4.4):
- username: 3-32 chars, ``[a-zA-Z0-9_-]`` only, required and non-empty
- registration password: 8-72 chars (72 is the bcrypt input byte limit)
- password change ``new_password``: 8-128 chars with complexity (at least one
  uppercase letter, one lowercase letter, one digit, and one special character)
"""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

# Username: 3-32 characters, letters, digits, hyphens, and underscores only.
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32

# Registration password: 8-72 characters (72 is the bcrypt input byte limit).
REGISTER_PASSWORD_MIN_LENGTH = 8
REGISTER_PASSWORD_MAX_LENGTH = 72

# Password-change policy: 8-128 characters with complexity requirements.
NEW_PASSWORD_MIN_LENGTH = 8
NEW_PASSWORD_MAX_LENGTH = 128

# Special character = anything that is not a letter or a digit.
_SPECIAL_CHARACTER_PATTERN = re.compile(r"[^A-Za-z0-9]")


def _validate_username(value: str) -> str:
    """Validate username format (requirements 1.6, 1.7)."""
    if not value:
        raise ValueError("username must not be empty")
    if len(value) < USERNAME_MIN_LENGTH or len(value) > USERNAME_MAX_LENGTH:
        raise ValueError(
            f"username must be between {USERNAME_MIN_LENGTH} and "
            f"{USERNAME_MAX_LENGTH} characters"
        )
    if not USERNAME_PATTERN.match(value):
        raise ValueError(
            "username may only contain letters, digits, hyphens, and underscores"
        )
    return value


def _validate_register_password(value: str) -> str:
    """Validate registration password length (requirements 1.4, 1.7)."""
    if not value:
        raise ValueError("password must not be empty")
    if (
        len(value) < REGISTER_PASSWORD_MIN_LENGTH
        or len(value) > REGISTER_PASSWORD_MAX_LENGTH
    ):
        raise ValueError(
            f"password must be between {REGISTER_PASSWORD_MIN_LENGTH} and "
            f"{REGISTER_PASSWORD_MAX_LENGTH} characters"
        )
    return value


def _validate_new_password(value: str) -> str:
    """Validate password-change policy (requirements 4.1, 4.4)."""
    if not value:
        raise ValueError("new_password must not be empty")
    if (
        len(value) < NEW_PASSWORD_MIN_LENGTH
        or len(value) > NEW_PASSWORD_MAX_LENGTH
    ):
        raise ValueError(
            f"new_password must be between {NEW_PASSWORD_MIN_LENGTH} and "
            f"{NEW_PASSWORD_MAX_LENGTH} characters"
        )
    if not re.search(r"[A-Z]", value):
        raise ValueError("new_password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", value):
        raise ValueError("new_password must contain at least one lowercase letter")
    if not re.search(r"\d", value):
        raise ValueError("new_password must contain at least one digit")
    if not _SPECIAL_CHARACTER_PATTERN.search(value):
        raise ValueError("new_password must contain at least one special character")
    return value


class RegisterRequest(BaseModel):
    """Registration payload: username + password with format/length validation."""

    username: str
    password: str
    captcha_id: str | None = None
    captcha_answer: str | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return _validate_username(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_register_password(value)


class LoginRequest(BaseModel):
    """Login payload. Fields are required and non-empty; no format checks so that
    invalid credentials yield a single generic error (requirement 2.2)."""

    username: str
    password: str
    captcha_id: str | None = None
    captcha_answer: str | None = None

    @field_validator("username", "password")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be empty")
        return value


class PasswordChangeRequest(BaseModel):
    """Password change payload: current password plus a policy-compliant new one."""

    current_password: str
    new_password: str

    @field_validator("current_password")
    @classmethod
    def validate_current_password(cls, value: str) -> str:
        if not value:
            raise ValueError("current_password must not be empty")
        return value

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return _validate_new_password(value)


class UserResponse(BaseModel):
    """Response model describing a user record.

    Field names mirror the ``users`` table created in ``auth_db.py``
    (``id``, ``username``, ``created_at``) so a row can be mapped directly.
    """

    id: str
    username: str
    created_at: str
