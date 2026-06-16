"""Tests for the users directory endpoint (GET /api/users)."""

from httpx import AsyncClient

USERS_URL = "/api/users"


async def test_list_users_requires_auth(client: AsyncClient) -> None:
    resp = await client.get(USERS_URL)
    assert resp.status_code == 401


async def test_list_users_excludes_self(client: AsyncClient, register_user) -> None:
    headers_a, user_a = await register_user(email="a@hemut.com", display_name="Alice")
    _, user_b = await register_user(email="b@hemut.com", display_name="Bob")

    resp = await client.get(USERS_URL, headers=headers_a)
    assert resp.status_code == 200

    ids = [u["id"] for u in resp.json()]
    assert user_b["id"] in ids
    assert user_a["id"] not in ids  # the caller is never in their own roster


async def test_list_users_omits_sensitive_fields(
    client: AsyncClient, register_user
) -> None:
    headers_a, _ = await register_user(email="a@hemut.com", display_name="Alice")
    await register_user(email="b@hemut.com", display_name="Bob")

    resp = await client.get(USERS_URL, headers=headers_a)
    assert resp.status_code == 200
    rows = resp.json()
    assert rows, "expected at least one other user"
    for row in rows:
        assert set(row.keys()) == {"id", "email", "display_name"}
        assert "password_hash" not in row
