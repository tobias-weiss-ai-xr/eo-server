"""WOPI authentication.

Two mechanisms are supported:

1. **Access-token mode** — the WOPI host passes `?access_token=<jwt>` on
   every call. We validate the JWT with our shared secret (`[security]
   jwt_secret`). This mirrors how OCIS signs WopiContext tokens.
2. **Bearer mode** — `Authorization: Bearer <jwt>` as an alternative.

For host-mode calls (CheckFileInfo/GetFile/PutFile) we accept the same
token; in client mode we forward the token OCIS gives us to the remote
WOPI host instead (see `src/editor/session.py`).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

from ..lib.crypto import decode_token
from .protocol import WopiError

#: PBKDF2-SHA512 derivation for document-protection passwords. 100k
#: iterations is the OWASP floor for that KDF; per-document random salt is
#: generated server-side, so the stored value is a hash, never the password.
_PROTECTION_ITERATIONS = 100_000
_PROTECTION_DKLEN = 32


def hash_protection_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    """Derive a document-protection password hash -> (salt_hex, hash_hex).

    A fresh random salt is minted per document unless one is supplied (the
    verify path recomputes with the stored salt). The derived hash lives in
    the DOCX ``w:documentProtection`` and is verified server-side only.
    """
    if salt_hex is None:
        salt_hex = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha512",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        _PROTECTION_ITERATIONS,
        dklen=_PROTECTION_DKLEN,
    )
    return salt_hex, dk.hex()


def verify_protection_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """Constant-time check of a supplied password against a stored hash."""
    if not password or not salt_hex or not hash_hex:
        return False
    try:
        dk = hashlib.pbkdf2_hmac(
            "sha512",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            _PROTECTION_ITERATIONS,
            dklen=_PROTECTION_DKLEN,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex().lower(), hash_hex.lower())


def token_from_request(request: Request) -> str | None:
    """Extract an access token from query string or Authorization header."""
    token = request.query_params.get("access_token")
    if token:
        return token

    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


def require_auth(request: Request, secret: str) -> dict:
    """Validate the caller's WOPI token; returns its claims.

    Raises WopiError(401, ...) when the token is missing or invalid.
    """
    token = token_from_request(request)
    if not token:
        raise WopiError(401, "Missing access_token")
    try:
        return decode_token(secret, token)
    except Exception as exc:  # PyJWT raises a family of errors
        raise WopiError(401, f"Invalid access_token: {exc}") from exc


def auth_dependency(secret: str):
    """FastAPI dependency factory that authenticates a request."""

    async def _check(request: Request):
        try:
            return require_auth(request, secret)
        except WopiError as err:
            return JSONResponse(status_code=err.status, content={"error": err.message})

    return _check
