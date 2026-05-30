"""Pydantic models for project templates.

This module is intentionally a standalone module (``templates_models.py``) rather
than a ``models/templates.py`` package member. The existing ``cairn.server.models``
is a single module (``models.py``) that is imported across the dispatcher and
server (``from cairn.server.models import ...``). Introducing a ``models/``
package would shadow that module and break those imports, so these models live
in their own additive module instead -- mirroring the convention established by
``auth_models.py``, ``vulnerabilities_models.py``, and ``workers_models.py``.

The field shapes follow design.md (New Pydantic Models section, Template models).
``TemplateResponse`` is the shape returned by ``GET /api/templates`` for both
built-in templates (``is_builtin=True``, ``user_id=None``) and a user's custom
templates (``is_builtin=False`` with the owning ``user_id``). ``CreateTemplateRequest``
is the payload for ``POST /api/templates``.

Validation rules (see requirements 13.1, 13.6, and 12.2):
- title / origin / goal: each between 1 and 200 characters
- hints: between 0 and 10 items, each a ``{content, creator}`` mapping
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

# A template's title, origin, and goal are each constrained to 1-200 characters
# (requirement 13.1, design.md Template models).
TEXT_MIN_LENGTH = 1
TEXT_MAX_LENGTH = 200

# A custom template may carry between 0 and 10 initial hints (design.md
# CreateTemplateRequest; requirement 12.2 covers the 1-10 hints a template
# pre-populates).
HINTS_MAX_ITEMS = 10


def _validate_text_field(value: str, field_name: str) -> str:
    """Validate a 1-200 character template text field (requirement 13.1)."""
    if len(value) < TEXT_MIN_LENGTH or len(value) > TEXT_MAX_LENGTH:
        raise ValueError(
            f"{field_name} must be between {TEXT_MIN_LENGTH} and "
            f"{TEXT_MAX_LENGTH} characters"
        )
    return value


class TemplateResponse(BaseModel):
    """A project template as returned to the client.

    Represents both built-in templates (``is_builtin=True`` with ``user_id`` left
    as ``None``) and a user's saved custom templates (``is_builtin=False`` with
    the owning ``user_id`` set). ``hints`` is a list of ``{content, creator}``
    mappings.
    """

    id: str
    title: str
    origin: str
    goal: str
    hints: list[dict[str, str]]
    is_builtin: bool
    user_id: str | None = None


class CreateTemplateRequest(BaseModel):
    """Payload for creating a custom template.

    Enforces the template field constraints from design.md / requirement 13.1:
    ``title``, ``origin``, and ``goal`` are each 1-200 characters, and ``hints``
    holds between 0 and 10 ``{content, creator}`` mappings.
    """

    title: str
    origin: str
    goal: str
    hints: list[dict[str, str]] = []

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _validate_text_field(value, "title")

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return _validate_text_field(value, "origin")

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        return _validate_text_field(value, "goal")

    @field_validator("hints")
    @classmethod
    def validate_hints(cls, value: list[dict[str, str]]) -> list[dict[str, str]]:
        if len(value) > HINTS_MAX_ITEMS:
            raise ValueError(f"hints must contain at most {HINTS_MAX_ITEMS} items")
        return value
