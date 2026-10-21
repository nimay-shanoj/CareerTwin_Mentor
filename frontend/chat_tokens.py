"""Single-use, short-lived tokens for the raw TCP chat client.

The browser session cookie is deliberately *not* used on the TCP channel: a
cookie would need a cookie-aware HTTP layer on a raw socket, and it would also
mean the raw TCP port could be used to replay a session. Instead the CLI
client authenticates over HTTP, receives a signed token, and redeems it once
inside the ``HELLO`` frame.

Token properties
----------------
* signed with ``itsdangerous.URLSafeTimedSerializer`` (HMAC-SHA256)
* bound to a user id
* 60 second TTL (configurable)
* **single use** – redeeming it marks the nonce as spent
"""

import secrets
import threading
import time

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


class TokenError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ChatTokenStore:
    """Issues and redeems single-use chat tokens."""

    def __init__(self, secret_key, salt="careertwin-chat-token", ttl=60):
        self._serializer = URLSafeTimedSerializer(secret_key, salt=salt)
        self._ttl = ttl
        self._lock = threading.Lock()
        # nonce -> expiry timestamp
        self._spent = {}

    @property
    def ttl(self):
        return self._ttl

    def issue(self, user_id):
        """Mint a token for a user."""
        nonce = secrets.token_hex(16)
        payload = {"uid": int(user_id), "nid": nonce, "iat": int(time.time())}
        token = self._serializer.dumps(payload)
        return {"token": token, "expires_in": self._ttl}

    def redeem(self, token):
        """Validate and burn a token, returning the user id.

        Raises TokenError with a stable code for every failure mode.
        """
        if not token or not isinstance(token, str):
            raise TokenError("MISSING_TOKEN", "A chat token is required.")

        try:
            payload = self._serializer.loads(token, max_age=self._ttl)
        except SignatureExpired:
            raise TokenError("TOKEN_EXPIRED", "This chat token has expired.")
        except BadSignature:
            raise TokenError("TOKEN_INVALID", "This chat token is not valid.")

        if not isinstance(payload, dict) or "uid" not in payload or "nid" not in payload:
            raise TokenError("TOKEN_INVALID", "This chat token is not valid.")

        nonce = payload["nid"]
        now = time.time()
        with self._lock:
            self._purge(now)
            if nonce in self._spent:
                raise TokenError("TOKEN_USED", "This chat token has already been used.")
            self._spent[nonce] = now + self._ttl

        return int(payload["uid"])

    def _purge(self, now):
        expired = [n for n, exp in self._spent.items() if exp < now]
        for nonce in expired:
            self._spent.pop(nonce, None)

    def reset(self):
        with self._lock:
            self._spent.clear()


# The application-wide store, configured from the app factory.
chat_token_store = ChatTokenStore("unconfigured", ttl=60)
