"""
Główna klasa serwera - skleja wszystkie komponenty i startuje nasłuchiwanie TCP z TLS.

OXSPServer odpowiada za:
  - konfigurację logowania i bazy danych
  - załadowanie certyfikatu TLS i stworzenie SSL kontekstu
  - uruchomienie serwera TCP
  - tworzenie instancji ClientHandler (z wstrzykniętymi zależnościami)
"""
from __future__ import annotations

import asyncio
import logging
import ssl

from server.config    import HOST, PORT, TLS_CERT, TLS_KEY, configure_logging
from server.database  import Database
from server.handler   import ClientHandler
from server.rate_limit import RateLimiter
from server.session   import SessionStore
from server.config    import RATE_AUTH_PER_MIN

log = logging.getLogger(__name__)


class OXSPServer:
    """
    Orkiestrator całej aplikacji serwerowej.

    Jedna instancja = jedna baza danych + jeden SessionStore + jeden auth_limiter
    współdzielone między wszystkimi połączeniami.

    Dependency Inversion: tworzy zależności i wstrzykuje je do handlerów.
    """

    def __init__(self, host: str = HOST, port: int = PORT):
        self.host  = host
        self.port  = port
        self.db    = Database()
        self.store = SessionStore()
        # rate limiter dla AUTH - współdzielony między sesjami (per IP)
        self._auth_limiter = RateLimiter(max_count=RATE_AUTH_PER_MIN)

    async def start(self) -> None:
        """Inicjalizuje i uruchamia serwer TCP z TLS. Blokuje aż do Ctrl+C."""
        configure_logging()
        self.db.init()

        ssl_ctx = self._build_ssl_context()

        server = await asyncio.start_server(
            self._on_connect,
            self.host,
            self.port,
            backlog=10,
            ssl=ssl_ctx,
        )

        log.info(f"Serwer OXSP (TLS) nasłuchuje na {self.host}:{self.port}")
        log.info(f"Certyfikat: {TLS_CERT}")
        log.info("Naciśnij Ctrl+C aby zatrzymać. Logi → server.log")

        async with server:
            await server.serve_forever()

    def _build_ssl_context(self) -> ssl.SSLContext:
        """
        Tworzy SSL kontekst serwera z certyfikatem i kluczem prywatnym.
        Self-signed certyfikat wygenerowany przez setup_tls.py.
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            ctx.load_cert_chain(TLS_CERT, TLS_KEY)
        except FileNotFoundError:
            log.error(f"Brak pliku certyfikatu! Uruchom najpierw: python setup_tls.py")
            raise
        return ctx

    async def _on_connect(self, reader, writer) -> None:
        """
        Callback wywoływany przez asyncio dla każdego nowego połączenia TCP.
        TLS handshake jest obsługiwany automatycznie przez asyncio przed tym callbackiem.
        """
        handler = ClientHandler(reader, writer, self.db, self.store, self._auth_limiter)
        await handler.handle()
