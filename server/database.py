"""
Warstwa dostępu do bazy danych SQLite.

Single Responsibility: ta klasa TYLKO czyta i pisze do bazy.
Nie wie nic o sesjach, grach ani protokole sieciowym.

Dzięki temu jak kiedyś będziemy chcieli zamienić SQLite na PostgreSQL,
zmieniamy tylko ten plik - reszta kodu nie zauważy różnicy.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3

from server.config import DB_FILE

log = logging.getLogger(__name__)


class Database:
    """
    Enkapsuluje wszystkie operacje na SQLite.

    Dlaczego klasa zamiast globalnych funkcji?
    Bo tak możemy łatwo przekazać instancję do klas które jej potrzebują
    (Dependency Injection) i ewentualnie w testach podmienić na mock.
    """

    def __init__(self, path: str = DB_FILE):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        """
        Jedno centralne miejsce do otwierania połączenia z bazą (DRY).
        Zamiast pisać sqlite3.connect(DB_FILE) w pięciu miejscach,
        piszemy self._connect() i mamy pewność że zawsze użyjemy właściwego pliku.
        """
        return sqlite3.connect(self.path)

    def init(self) -> None:
        """
        Tworzy schemat bazy i ładuje konta testowe.

        IF NOT EXISTS i INSERT OR IGNORE sprawiają że tę metodę można
        wywoływać wielokrotnie bezpiecznie - nie nadpisze istniejących danych.
        """
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username      TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL
                )
                """
            )
            # konta testowe - nasze własne loginy do prezentacji
            test_accounts = [
                ("damian",  _sha256("damian")),
                ("piotrek", _sha256("piotrek")),
            ]
            conn.executemany("INSERT OR IGNORE INTO users VALUES (?,?)", test_accounts)

        log.info("Baza gotowa. Konta: damian/damian  piotrek/piotrek")

    def verify_credentials(self, username: str, pw_hash: str) -> bool:
        """
        Sprawdza czy login + hash hasła zgadzają się z zapisem w bazie.

        Nigdy nie porównujemy plaintext haseł - tylko hashe SHA-256.
        Parametryzowane zapytanie (?) chroni przed SQL Injection.
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT password_hash FROM users WHERE username = ?",
                    (username,),
                ).fetchone()
            # row=None jeśli user nie istnieje, row[0] to hash z bazy
            return bool(row and row[0] == pw_hash)
        except sqlite3.Error as e:
            log.error(f"Błąd bazy danych przy weryfikacji '{username}': {e}")
            return False  # błąd DB traktujemy konserwatywnie jak złe dane


def _sha256(text: str) -> str:
    """Prywatna funkcja modułu - hash SHA-256 jako hex string."""
    return hashlib.sha256(text.encode()).hexdigest()
