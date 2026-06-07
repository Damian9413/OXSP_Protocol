"""
Zarządzanie stanem serwera w pamięci RAM: sesje, kolejka i aktywne gry.

Zamiast globalnych słowników (sessions = {}, queue = [], games = {})
mamy jeden obiekt SessionStore który hermetyzuje stan.
Korzyści: łatwiejsze testowanie, brak globalnego stanu, jedna klasa = jedna odpowiedzialność.
"""
from __future__ import annotations

import logging
import uuid

log = logging.getLogger(__name__)


class SessionStore:
    """
    Przechowuje i zarządza całym stanem aplikacji serwerowej.

    Trzy "obszary" danych:
      _sessions - aktywne sesje zalogowanych graczy (token -> dane)
      _queue    - gracze czekający na sparowanie w matchmakingu
      _games    - aktywne rozgrywki (game_id -> stan planszy + gracze + timer)
    """

    def __init__(self):
        self._sessions: dict = {}
        self._queue:    list = []
        self._games:    dict = {}

    # ── sesje ─────────────────────────────────────────────────────────────────

    def add_session(self, token: str, writer, username: str) -> None:
        """Rejestruje nową sesję po pomyślnym AUTH."""
        self._sessions[token] = {
            "writer":   writer,     # potrzebny do wysyłania wiadomości do tego gracza
            "username": username,
            "state":    "AUTHENTICATED",
            "game_id":  None,
            "symbol":   None,
        }

    def remove_session(self, token: str) -> None:
        """Usuwa sesję i wyciąga gracza z kolejki matchmakingu."""
        self._sessions.pop(token, None)
        if token in self._queue:
            self._queue.remove(token)

    def get_session(self, token: str) -> dict | None:
        return self._sessions.get(token)

    # ── matchmaking ───────────────────────────────────────────────────────────

    @property
    def queue_size(self) -> int:
        """Liczba graczy aktualnie czekających na grę."""
        return len(self._queue)

    def enqueue(self, token: str) -> None:
        """
        Wstawia gracza do kolejki matchmakingu.
        Sprawdzamy czy już jest w kolejce żeby uniknąć duplikatów.
        """
        if token not in self._queue:
            self._sessions[token]["state"] = "QUEUED"
            self._queue.append(token)

    def try_match(self) -> tuple | None:
        """
        Próbuje sparować dwóch graczy z kolejki (FIFO - kto pierwszy ten lepszy).

        Pomija graczy którzy zdążyli się rozłączyć podczas oczekiwania.
        Jeśli tylko jeden z pary jest podłączony, wrzucamy go z powrotem na początek kolejki.

        Zwraca (token1, token2) lub None jeśli nie można dopasować.
        """
        while len(self._queue) >= 2:
            t1, t2 = self._queue.pop(0), self._queue.pop(0)
            t1_ok  = t1 in self._sessions
            t2_ok  = t2 in self._sessions

            if t1_ok and t2_ok:
                return t1, t2

            # przynajmniej jeden się rozłączył - żywego wrzucamy z powrotem
            for t, ok in ((t1, t1_ok), (t2, t2_ok)):
                if ok:
                    self._queue.insert(0, t)

        return None

    # ── gry ──────────────────────────────────────────────────────────────────

    def create_game(self, t1: str, t2: str) -> str:
        """
        Tworzy nową grę między dwoma graczami.
        t1 dostaje symbol X i wykonuje pierwszy ruch (spec §4.2).
        Zwraca wygenerowane game_id.
        """
        gid = str(uuid.uuid4())[:8]
        self._games[gid] = {
            "board":      [""] * 9,
            "turn":       t1,        # token gracza który teraz gra
            "p1":         t1,
            "p2":         t2,
            "move_timer": None,      # asyncio.Task timera ruchu (ustawiany przez handler)
        }
        self._sessions[t1].update({"game_id": gid, "symbol": "X", "state": "IN_GAME"})
        self._sessions[t2].update({"game_id": gid, "symbol": "O", "state": "IN_GAME"})
        return gid

    def get_game(self, gid: str) -> dict | None:
        return self._games.get(gid)

    def end_game(self, gid: str) -> dict | None:
        """
        Kończy grę: usuwa ją z pamięci, anuluje timer ruchu i resetuje stany sesji.
        Zwraca dane zakończonej gry (np. żeby zalogować wynik).
        """
        game = self._games.pop(gid, None)
        if game:
            # anulujemy timer ruchu jeśli jeszcze działa
            timer = game.get("move_timer")
            if timer and not timer.done():
                timer.cancel()
            for tok in (game["p1"], game["p2"]):
                if tok in self._sessions:
                    self._sessions[tok].update({"state": "FINISHED", "game_id": None})
        return game

    def set_move_timer(self, gid: str, task) -> None:
        """
        Ustawia (lub zastępuje) zadanie timera ruchu dla danej gry.
        Jeśli był stary timer, anuluje go najpierw.
        Zadanie (asyncio.Task) jest tworzone w handler.py i tu tylko przechowywane.
        """
        game = self._games.get(gid)
        if not game:
            return
        old = game.get("move_timer")
        if old and not old.done():
            old.cancel()
        game["move_timer"] = task

    def get_opponent(self, token: str, gid: str) -> str | None:
        """Zwraca token gracza po drugiej stronie planszy."""
        game = self._games.get(gid)
        if not game:
            return None
        return game["p2"] if token == game["p1"] else game["p1"]
