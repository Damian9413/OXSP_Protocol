"""
Uwierzytelnianie - generowanie i weryfikacja tokenów JWT.

Implementujemy JWT "ręcznie" bez zewnętrznych bibliotek (tylko stdlib).
Format: base64url(header).base64url(payload).base64url(HMAC-SHA256)

Dlaczego JWT zamiast zwykłego UUID?
  - Token zawiera weryfikowalne claims (kto, kiedy, do kiedy ważny)
  - Nie da się sfałszować bez klucza JWT_SECRET
  - Nawet bez tabeli sesji możemy zweryfikować kto jest właścicielem tokenu
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from server.config import JWT_SECRET


def create_jwt(username: str) -> str:
    """
    Tworzy token JWT z HMAC-SHA256 dla podanego użytkownika.
    Token jest ważny przez 1 godzinę.

    Struktura: header.payload.signature (każda część base64url bez paddingu)
    """
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({
        "sub": username,          # subject - kto jest właścicielem tokenu
        "jti": str(uuid.uuid4()), # JWT ID - unikalny, potrzebny do replay protection
        "iat": int(time.time()),  # issued at - kiedy wystawiony
        "exp": int(time.time()) + 3600,  # expiry - ważny 1 godzinę
    }).encode())
    signature = _b64url(_hmac_sign(f"{header}.{payload}"))
    return f"{header}.{payload}.{signature}"


def verify_jwt(token: str) -> dict | None:
    """
    Weryfikuje token JWT.

    Sprawdza:
      1. Czy podpis HMAC-SHA256 jest prawidłowy (nie sfałszowany)
      2. Czy token nie wygasł (exp > teraz)

    Zwraca payload (dict) jeśli OK, None jeśli token jest nieważny.

    hmac.compare_digest zamiast == chroni przed timing attacks
    (atakujący nie może mierzyć czasu porównania bajtów żeby odgadnąć klucz).
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header, payload_b64, signature = parts
        expected = _b64url(_hmac_sign(f"{header}.{payload_b64}"))

        if not hmac.compare_digest(signature, expected):
            return None  # podpis się nie zgadza - sfałszowany token

        data = json.loads(_b64url_decode(payload_b64))
        if int(time.time()) > data.get("exp", 0):
            return None  # token wygasł

        return data
    except Exception:
        return None


# ── prywatne funkcje pomocnicze ───────────────────────────────────────────────

def _hmac_sign(data: str) -> bytes:
    """Oblicza HMAC-SHA256 dla podanych danych."""
    return hmac.new(JWT_SECRET, data.encode(), hashlib.sha256).digest()


def _b64url(data: bytes) -> str:
    """Base64url encoding bez paddingu (=) - standard JWT."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    """Base64url decoding - dodajemy padding jeśli brakuje."""
    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (padding % 4))
