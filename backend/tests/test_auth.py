"""Integration tests for POST /api/auth/register and POST /api/auth/login.

Every test runs inside a DB transaction that rolls back at teardown, so
there is no persistent state between tests and no truncation scripts needed.
"""

import pytest
from httpx import AsyncClient

REGISTER_URL = "/api/auth/register"
LOGIN_URL = "/api/auth/login"

VALID_USER = {
    "email": "alice@hemut.com",
    "password": "password123",
    "display_name": "Alice",
}


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


async def test_register_success(client: AsyncClient) -> None:
    resp = await client.post(REGISTER_URL, json=VALID_USER)

    assert resp.status_code == 201
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == VALID_USER["email"]
    assert body["user"]["display_name"] == VALID_USER["display_name"]
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]


async def test_register_duplicate_email(client: AsyncClient) -> None:
    await client.post(REGISTER_URL, json=VALID_USER)
    resp = await client.post(REGISTER_URL, json=VALID_USER)

    assert resp.status_code == 400
    assert "already registered" in resp.json()["detail"].lower()


async def test_register_invalid_email(client: AsyncClient) -> None:
    resp = await client.post(
        REGISTER_URL,
        json={**VALID_USER, "email": "not-an-email"},
    )
    assert resp.status_code == 422


async def test_register_short_password(client: AsyncClient) -> None:
    resp = await client.post(
        REGISTER_URL,
        json={**VALID_USER, "password": "short"},
    )
    assert resp.status_code == 422


async def test_register_blank_display_name(client: AsyncClient) -> None:
    resp = await client.post(
        REGISTER_URL,
        json={**VALID_USER, "display_name": "   "},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def test_login_success(client: AsyncClient) -> None:
    await client.post(REGISTER_URL, json=VALID_USER)

    resp = await client.post(
        LOGIN_URL,
        json={"email": VALID_USER["email"], "password": VALID_USER["password"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["user"]["email"] == VALID_USER["email"]


async def test_login_wrong_password(client: AsyncClient) -> None:
    await client.post(REGISTER_URL, json=VALID_USER)

    resp = await client.post(
        LOGIN_URL,
        json={"email": VALID_USER["email"], "password": "wrongpassword"},
    )

    assert resp.status_code == 401
    # Deliberately vague — must NOT reveal which field was wrong
    detail = resp.json()["detail"]
    assert "invalid email or password" in detail.lower()


async def test_login_unknown_email(client: AsyncClient) -> None:
    resp = await client.post(
        LOGIN_URL,
        json={"email": "ghost@hemut.com", "password": "password123"},
    )

    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert "invalid email or password" in detail.lower()


async def test_login_missing_password(client: AsyncClient) -> None:
    resp = await client.post(LOGIN_URL, json={"email": "alice@hemut.com"})
    assert resp.status_code == 422
