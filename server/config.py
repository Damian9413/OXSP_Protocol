"""
Konfiguracja serwera OXSP.

Wszystkie stałe konfiguracyjne w jednym miejscu - żeby zmienić port
wystarczy edytować ten plik, nie szukać po całym projekcie (zasada DRY).
"""

import logging

# adres i port nasłuchiwania
HOST = "127.0.0.1"
PORT = 8765

# maksymalny rozmiar jednej wiadomości w bajtach
# chroni przed atakami flooding / memory exhaustion
MAX_MSG = 4096

# plik bazy danych SQLite (tworzony automatycznie przy pierwszym starcie)
DB_FILE = "oxsp.db"

# pola które MUSZĄ być w każdej przychodzącej wiadomości (spec OXSP §2.1)
# jako tuple żeby przypadkowo nie zmodyfikować
REQUIRED_FIELDS = ("type", "msg_id", "timestamp", "session_token")

# ── bezpieczeństwo ────────────────────────────────────────────────────────────

# klucz do podpisywania tokenów JWT - zmienić przed wdrożeniem!
# Na produkcji pobierać ze zmiennej środowiskowej: os.environ["JWT_SECRET"].encode()
JWT_SECRET: bytes = b"oxsp-jwt-secret-ZMIEN-PRZED-WDROZENIEM-na-produkcji"

# ── TLS ───────────────────────────────────────────────────────────────────────

TLS_CERT = "tls/cert.pem"   # certyfikat serwera
TLS_KEY  = "tls/key.pem"    # klucz prywatny serwera

# ── timeouty i keepalive (spec OXSP §3.2) ─────────────────────────────────────

PING_INTERVAL = 30   # sekund bezczynności po których serwer wysyła PING
PONG_TIMEOUT  = 10   # sekund na odpowiedź PONG (po tym czasie - rozłączenie)
MOVE_TIMEOUT  = 60   # sekund na wykonanie ruchu (po tym czasie - walkower)

# ── rate limiting (spec OXSP §5.1) ───────────────────────────────────────────

RATE_MSG_PER_MIN  = 60   # max wiadomości na minutę na sesję
RATE_AUTH_PER_MIN = 5    # max prób AUTH na minutę na IP


def configure_logging() -> None:
    """
    Konfiguruje logger aplikacji.
    Logi idą jednocześnie na konsolę i do pliku server.log
    żeby można było potem przejrzeć co się działo.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [SERVER] %(levelname)-8s %(message)s",
        handlers=[
            logging.FileHandler("server.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
