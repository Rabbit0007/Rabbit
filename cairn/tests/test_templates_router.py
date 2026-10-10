"""Unit tests for the templates router (spec task 7.5).

Target: ``cairn.server.routers.templates`` mounted on a FastAPI app together
with the auth router and the shared ``require_auth`` dependency, mirroring how
``cairn.server.app`` wires the protected routers.

Covers requirements 12.1-12.5 (built-in templates) and 13.1-13.7 (custom
templates: create, list, delete, ownership, per-user limit, validation).

Environment notes (consistent with the existing auth/vulnerability tests):

- Custom templates are user-scoped: the router resolves the owning ``user_id``
  from ``request.state.user``, which the ``require_auth`` middleware only injects
  once ``CAIRN_INTERNAL_TOKEN`` is configured. The ``templates_app`` fixture sets
  that env var so the templates endpoints enforce cookie auth and bind each
  custom template to the registered user (rather than the shared anonymous
  owner). Tests obtain a session cookie by registering through
  ``/api/auth/register``.
- The auth router sets a ``Secure`` cookie, so every client talks to an
  ``https`` origin (``base_url="https://testserver"``) for the cookie to
  round-trip.
- ``db.configure()`` short-circuits once ``db._db_path`` is set; the shared
  ``temp_db`` fixture (conftest.py) resets that module-global so every test gets
  a fresh, isolated database.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.middleware.auth import INTERNAL_TOKEN_ENV, require_auth
from cairn.server.routers.templates import MAX_TEMPLATES_PER_USER
from cairn.server.templates_service import BUILTIN_TEMPLATES

from .conftest import BASE_URL

# The built-in templates that must always be present (requirement 12.1).
EXPECTED_BUILTIN_TITLES = {template["title"] for template in BUILTIN_TEMPLATES}

# A long-but-valid 200-char string and an over-the-limit 201-char string used to
# probe the 1-200 character field bounds (requirement 13.1).
TEXT_200 = "a" * 200
TEXT_201 = "a" * 201


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def templates_app(temp_db, monkeypatch) -> FastAPI:
    """A FastAPI app mounting the auth router plus the protected templates router.

    Mirrors ``cairn.server.app``: the auth router is mounted openly (its own
    endpoints obtain sessions) while the templates router is guarded by the
    shared ``require_auth`` dependency. ``CAIRN_INTERNAL_TOKEN`` is set so
    ``require_auth`` enforces the session cookie and injects ``request.state.user``,
    binding custom templates to the registered user.
    """
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, "test-internal-token")

    from cairn.server.routers import auth, templates

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(templates.router, dependencies=[Depends(require_auth)])
    return app


@pytest.fixture
def make_client(templates_app):
    """Factory returning fresh TestClients (independent cookie jars)."""

    def _make() -> TestClient:
        return TestClient(templates_app, base_url=BASE_URL)

    return _make


@pytest.fixture
def client(make_client) -> TestClient:
    """A single TestClient over an https origin (Secure cookie round-trips)."""
    return make_client()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_user_counter = 0


def captcha_payload(client):
    response = client.get("/api/auth/captcha")
    assert response.status_code == 200
    body = response.json()
    nums = [int(value) for value in re.findall(r"\d+", body["question"])]
    assert len(nums) == 2
    return {"captcha_id": body["captcha_id"], "captcha_answer": str(sum(nums))}


def register_user(client, username=None, password="password123"):
    """Register a unique user and return the created user's id.

    The returned id matches the ``user_id`` the templates router stores for that
    user's custom templates (both come from the same ``users`` row).
    """
    global _user_counter
    if username is None:
        _user_counter += 1
        username = f"user_{_user_counter}"
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, **captcha_payload(client)},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_template(client, title="My Template", origin="Origin fact",
                    goal="Goal fact", hints=None):
    payload = {"title": title, "origin": origin, "goal": goal}
    if hints is not None:
        payload["hints"] = hints
    return client.post("/api/templates", json=payload)


def hint(content, creator="user"):
    return {"content": content, "creator": creator}


def _insert_template_rows(user_id: str, count: int) -> None:
    """Directly insert ``count`` custom templates owned by ``user_id``.

    Used to pre-fill a user up to the per-user cap without making ``count`` HTTP
    round-trips, keeping the limit test fast while still exercising the real
    table the router counts against.
    """
    with db.get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO templates
                (id, user_id, title, origin, goal, hints_json, created_at)
            VALUES (?, ?, ?, ?, ?, '[]', '2024-01-01T00:00:00Z')
            """,
            [
                (f"tmpl_seed_{i}", user_id, f"Seed {i}", "o", "g")
                for i in range(count)
            ],
        )


# ---------------------------------------------------------------------------
# GET /api/templates: built-in templates (requirements 12.1, 12.2, 13.2)
# ---------------------------------------------------------------------------


def test_get_templates_always_returns_four_builtins(client):
    """The four built-in templates are always returned (requirement 12.1)."""
    register_user(client)

    response = client.get("/api/templates")
    assert response.status_code == 200
    body = response.json()

    titles = {t["title"] for t in body if t["is_builtin"]}
    assert EXPECTED_BUILTIN_TITLES.issubset(titles)
    # No custom templates yet, so the only entries are the built-ins.
    assert len(body) == len(BUILTIN_TEMPLATES)


def test_builtins_are_labelled_builtin_with_no_user(client):
    """Built-ins carry ``is_builtin=True`` and ``user_id=None`` (req 12.2, 13.2)."""
    register_user(client)

    body = client.get("/api/templates").json()
    builtins = [t for t in body if t["title"] in EXPECTED_BUILTIN_TITLES]
    assert len(builtins) == 4
    for t in builtins:
        assert t["is_builtin"] is True
        assert t["user_id"] is None
        # Each built-in carries title/origin/goal and 1-10 hints (req 12.2).
        assert t["title"] and t["origin"] and t["goal"]
        assert 1 <= len(t["hints"]) <= 10


def test_builtins_returned_for_every_user(client, make_client):
    """Built-ins are user-independent: a second user sees the same four."""
    register_user(client)
    other = make_client()
    register_user(other)

    a_titles = {t["title"] for t in client.get("/api/templates").json() if t["is_builtin"]}
    b_titles = {t["title"] for t in other.get("/api/templates").json() if t["is_builtin"]}
    assert EXPECTED_BUILTIN_TITLES.issubset(a_titles)
    assert a_titles == b_titles


# ---------------------------------------------------------------------------
# POST /api/templates: create custom template (requirements 13.1, 13.2)
# ---------------------------------------------------------------------------


def test_create_template_returns_user_created_template(client):
    """A created template is returned labelled as user-created (req 13.1)."""
    user_id = register_user(client)

    response = create_template(
        client,
        title="Recon Playbook",
        origin="Target at example.com",
        goal="Map the attack surface",
        hints=[hint("Enumerate subdomains")],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"].startswith("tmpl_")
    assert body["title"] == "Recon Playbook"
    assert body["origin"] == "Target at example.com"
    assert body["goal"] == "Map the attack surface"
    assert body["hints"] == [hint("Enumerate subdomains")]
    assert body["is_builtin"] is False
    assert body["user_id"] == user_id


def test_created_template_listed_alongside_builtins(client):
    """A custom template appears in GET next to the built-ins (req 13.2)."""
    user_id = register_user(client)
    created = create_template(client, title="Custom One").json()

    body = client.get("/api/templates").json()
    # Built-ins still present plus exactly one custom template.
    builtin_titles = {t["title"] for t in body if t["is_builtin"]}
    assert EXPECTED_BUILTIN_TITLES.issubset(builtin_titles)

    customs = [t for t in body if not t["is_builtin"]]
    assert len(customs) == 1
    assert customs[0]["id"] == created["id"]
    assert customs[0]["title"] == "Custom One"
    assert customs[0]["user_id"] == user_id
    assert len(body) == len(BUILTIN_TEMPLATES) + 1


def test_custom_templates_are_scoped_per_user(client, make_client):
    """A user only sees their own custom templates (req 13.2)."""
    register_user(client)
    create_template(client, title="Alice Template")

    other = make_client()
    register_user(other)
    other_body = other.get("/api/templates").json()

    # Bob sees the built-ins but none of Alice's custom templates.
    assert [t for t in other_body if not t["is_builtin"]] == []
    assert len(other_body) == len(BUILTIN_TEMPLATES)


def test_create_template_with_no_hints_defaults_to_empty(client):
    """Hints are optional; omitting them stores an empty list (req 13.1)."""
    register_user(client)
    body = create_template(client, hints=None).json()
    assert body["hints"] == []


# ---------------------------------------------------------------------------
# DELETE /api/templates/{id} (requirements 13.4, 13.5)
# ---------------------------------------------------------------------------


def test_delete_own_custom_template(client):
    """A user can delete their own custom template (req 13.4)."""
    register_user(client)
    created = create_template(client, title="To Delete").json()

    delete = client.delete(f"/api/templates/{created['id']}")
    assert delete.status_code == 204

    # It is gone from the listing (only built-ins remain).
    body = client.get("/api/templates").json()
    assert [t for t in body if not t["is_builtin"]] == []
    assert len(body) == len(BUILTIN_TEMPLATES)


def test_delete_template_owned_by_another_user_is_forbidden(client, make_client):
    """Deleting another user's template is rejected with 403 and left intact
    (req 13.5)."""
    register_user(client)
    created = create_template(client, title="Alice Owns This").json()

    attacker = make_client()
    register_user(attacker)
    delete = attacker.delete(f"/api/templates/{created['id']}")
    assert delete.status_code == 403

    # The template still exists for the owner.
    owner_customs = [t for t in client.get("/api/templates").json() if not t["is_builtin"]]
    assert len(owner_customs) == 1
    assert owner_customs[0]["id"] == created["id"]


def test_delete_builtin_template_is_rejected(client):
    """Built-in ids are not stored, so deleting one is rejected (404) and the
    built-in remains (requirement 12 built-ins are immutable)."""
    register_user(client)
    builtin_id = BUILTIN_TEMPLATES[0]["id"]

    delete = client.delete(f"/api/templates/{builtin_id}")
    assert delete.status_code == 404

    # The built-in is still returned.
    titles = {t["title"] for t in client.get("/api/templates").json() if t["is_builtin"]}
    assert EXPECTED_BUILTIN_TITLES.issubset(titles)


def test_delete_unknown_template_returns_404(client):
    """Deleting a non-existent template id yields 404."""
    register_user(client)
    assert client.delete("/api/templates/tmpl_does_not_exist").status_code == 404


# ---------------------------------------------------------------------------
# Per-user limit of 50 templates (requirements 13.6, 13.7)
# ---------------------------------------------------------------------------


def test_create_beyond_limit_is_rejected_with_409(client):
    """Creating the 51st template for a user is rejected (req 13.6, 13.7)."""
    user_id = register_user(client)
    # Pre-fill the user up to the cap directly, then attempt one more via the API.
    _insert_template_rows(user_id, MAX_TEMPLATES_PER_USER)

    response = create_template(client, title="One Too Many")
    assert response.status_code == 409

    # The cap is unchanged: still exactly MAX_TEMPLATES_PER_USER custom templates.
    customs = [t for t in client.get("/api/templates").json() if not t["is_builtin"]]
    assert len(customs) == MAX_TEMPLATES_PER_USER


def test_create_at_limit_minus_one_still_succeeds(client):
    """The 50th template (reaching the cap exactly) is still allowed."""
    user_id = register_user(client)
    _insert_template_rows(user_id, MAX_TEMPLATES_PER_USER - 1)

    response = create_template(client, title="The Fiftieth")
    assert response.status_code == 201

    customs = [t for t in client.get("/api/templates").json() if not t["is_builtin"]]
    assert len(customs) == MAX_TEMPLATES_PER_USER


def test_limit_is_per_user_not_global(client, make_client):
    """One user at the cap does not block a different user from creating."""
    user_id = register_user(client)
    _insert_template_rows(user_id, MAX_TEMPLATES_PER_USER)
    assert create_template(client, title="Blocked").status_code == 409

    other = make_client()
    register_user(other)
    assert create_template(other, title="Allowed").status_code == 201


# ---------------------------------------------------------------------------
# Field validation (requirement 13.1): 1-200 chars; 0-10 hints -> 422
# ---------------------------------------------------------------------------


def test_create_empty_title_returns_422(client):
    register_user(client)
    assert create_template(client, title="").status_code == 422


def test_create_empty_origin_returns_422(client):
    register_user(client)
    assert create_template(client, origin="").status_code == 422


def test_create_empty_goal_returns_422(client):
    register_user(client)
    assert create_template(client, goal="").status_code == 422


def test_create_title_at_200_chars_is_allowed(client):
    register_user(client)
    assert create_template(client, title=TEXT_200).status_code == 201


def test_create_title_over_200_chars_returns_422(client):
    register_user(client)
    assert create_template(client, title=TEXT_201).status_code == 422


def test_create_origin_over_200_chars_returns_422(client):
    register_user(client)
    assert create_template(client, origin=TEXT_201).status_code == 422


def test_create_goal_over_200_chars_returns_422(client):
    register_user(client)
    assert create_template(client, goal=TEXT_201).status_code == 422


def test_create_with_ten_hints_is_allowed(client):
    register_user(client)
    hints = [hint(f"hint {i}") for i in range(10)]
    assert create_template(client, hints=hints).status_code == 201


def test_create_with_eleven_hints_returns_422(client):
    register_user(client)
    hints = [hint(f"hint {i}") for i in range(11)]
    assert create_template(client, hints=hints).status_code == 422


def test_create_missing_required_fields_returns_422(client):
    register_user(client)
    assert client.post("/api/templates", json={}).status_code == 422
    assert client.post("/api/templates", json={"title": "only title"}).status_code == 422
