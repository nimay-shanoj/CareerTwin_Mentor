"""Tests for the single-use chat handshake tokens."""

import time

import pytest

from chat_tokens import ChatTokenStore, TokenError


@pytest.fixture()
def store():
    return ChatTokenStore(secret_key="test-secret", ttl=60)


def test_a_token_redeems_once(store):
    token = store.issue(7)["token"]
    assert store.redeem(token) == 7


def test_a_token_cannot_be_redeemed_twice(store):
    token = store.issue(7)["token"]
    store.redeem(token)
    with pytest.raises(TokenError) as exc:
        store.redeem(token)
    assert exc.value.code == "TOKEN_USED"


def test_a_tampered_token_is_rejected(store):
    token = store.issue(7)["token"]
    tampered = token[:-4] + "aaaa"
    with pytest.raises(TokenError) as exc:
        store.redeem(tampered)
    assert exc.value.code in ("TOKEN_INVALID", "TOKEN_EXPIRED")


def test_a_missing_token_is_rejected(store):
    with pytest.raises(TokenError) as exc:
        store.redeem("")
    assert exc.value.code == "MISSING_TOKEN"


def test_a_token_expires():
    # `itsdangerous` compares whole seconds, so a TTL of 0 expires as soon
    # as the clock has moved past the second the token was issued in.
    short = ChatTokenStore(secret_key="test-secret", ttl=0)
    token = short.issue(1)["token"]
    time.sleep(1.05)
    with pytest.raises(TokenError) as exc:
        short.redeem(token)
    assert exc.value.code == "TOKEN_EXPIRED"


def test_two_tokens_for_the_same_user_are_independent(store):
    first = store.issue(1)["token"]
    second = store.issue(1)["token"]
    assert store.redeem(first) == 1
    assert store.redeem(second) == 1


def test_the_endpoint_issues_a_token(user_factory):
    client, user = user_factory("token@example.test", "Token User")
    response = client.post("/api/chat/token")
    assert response.status_code == 200
    data = response.get_json()
    assert "token" in data
    assert data["expires_in"] > 0


def test_the_endpoint_requires_a_login(client):
    assert client.post("/api/chat/token").status_code == 401
