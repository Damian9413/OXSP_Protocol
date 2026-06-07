"""
Obsługa jednego połączenia TCP przez cały jego cykl życia.

Ten moduł integruje wszystkie mechanizmy bezpieczeństwa:
  - JWT weryfikacja tokenów sesji
  - Replay protection (msg_id + timestamp)
  - Rate limiting (per sesja + per IP dla AUTH)
  - PING/PONG keepalive (serwer → PING co 30s, klient → PONG w 10s)
  - Move timeout (forfeit po 60s bezczynności)

Zasady:
  SRP - ClientHandler zajmuje się PROTOKOŁEM, nie logiką gry (engine.py) ani stanem (session.py)
  OCP - nowy typ wiadomości = nowa metoda _on_X + wpis w _dispatch_map
  DIP - zależności (db, store, auth_limiter) wstrzykiwane przez konstruktor
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from server.auth       import create_jwt, verify_jwt
from server.config     import (MAX_MSG, MOVE_TIMEOUT, PING_INTERVAL,
                               PONG_TIMEOUT, RATE_MSG_PER_MIN, REQUIRED_FIELDS)
from server.database   import Database
from server.engine     import GameEngine
from server.rate_limit import RateLimiter
from server.replay     import ReplayGuard
from server.session    import SessionStore
from shared.protocol   import build_frame

log = logging.getLogger(__name__)


class ClientHandler:
    """
    Obsługuje jedno połączenie TCP.

    Każde nowe połączenie dostaje własną instancję tego handlera.
    Instancja posiada własny ReplayGuard i per-sesyjny RateLimiter.
    auth_limiter jest współdzielony między wszystkimi połączeniami (per-IP).
    """

    def __init__(self, reader, writer, db: Database, store: SessionStore,
                 auth_limiter: RateLimiter):
        self.reader       = reader
        self.writer       = writer
        self.db           = db
        self.store        = store
        self.token        = None   # JWT token sesji - ustawiany po udanym AUTH
        self.addr         = writer.get_extra_info("peername")

        # bezpieczeństwo - każde połączenie ma własne instancje
        self._replay      = ReplayGuard()
        self._msg_limiter = RateLimiter(RATE_MSG_PER_MIN)    # 60 msg/min na sesję
        self._auth_limiter = auth_limiter                     # 5 AUTH/min na IP (współdzielony)

        # czas ostatniej aktywności - do obliczania kiedy wysłać PING
        self._last_msg_at: float = 0.0

        # słownik type->handler - Open/Closed: nowy typ = nowa metoda + wpis tutaj
        self._dispatch_map = {
            "HELLO":      self._on_hello,
            "AUTH":       self._on_auth,
            "QUEUE_JOIN": self._on_queue_join,
            "MOVE":       self._on_move,
            "PONG":       self._on_pong,
            "BYE":        self._on_bye,
        }

    async def handle(self) -> None:
        """Punkt wejścia - wywoływany przez OXSPServer dla każdego nowego połączenia."""
        log.info(f"Nowe połączenie: {self.addr}")
        try:
            await self._loop()
        except Exception as e:
            log.error(f"Nieoczekiwany błąd [{self.addr}]: {e}", exc_info=True)
        finally:
            await self._cleanup()

    # ── główna pętla z keepalive ──────────────────────────────────────────────

    async def _loop(self) -> None:
        """
        Czyta wiadomości i przetwarza je.
        Jeśli klient jest bezczynny przez PING_INTERVAL sekund - wysyła PING.
        Jeśli brak PONG w ciągu PONG_TIMEOUT - rozłącza.
        """
        self._last_msg_at = time.monotonic()

        while True:
            # ile czasu zostało do następnego PING
            idle    = time.monotonic() - self._last_msg_at
            timeout = max(PING_INTERVAL - idle, 0.5)

            try:
                raw = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
            except asyncio.TimeoutError:
                # klient bezczynny - czas na PING
                if not await self._do_keepalive():
                    log.info(f"PING timeout [{self.addr}] - zamykam połączenie")
                    break
                self._last_msg_at = time.monotonic()
                continue

            if not raw:
                break   # TCP EOF - klient zamknął połączenie

            self._last_msg_at = time.monotonic()

            msg, is_fatal = await self._parse(raw)
            if is_fatal:
                break
            if msg is None:
                continue   # błąd niekrytyczny - czytamy następną wiadomość

            if not await self._dispatch(msg):
                break

    async def _do_keepalive(self) -> bool:
        """
        Wysyła PING i czeka na PONG w ciągu PONG_TIMEOUT sekund.
        Zwraca True jeśli PONG przyszedł na czas, False jeśli timeout.

        Uproszczenie: jeśli klient wyśle cokolwiek innego zamiast PONG,
        traktujemy to jako naruszenie protokołu i rozłączamy.
        Na produkcji kolejkowałoby się inne wiadomości i czekało dalej na PONG.
        """
        log.info(f"Wysyłam PING do [{self.addr}]")
        await self._send("PING")
        try:
            data = await asyncio.wait_for(self.reader.readline(), timeout=PONG_TIMEOUT)
            if not data:
                return False
            msg = json.loads(data.decode("utf-8"))
            if msg.get("type") == "PONG":
                log.info(f"PONG otrzymany od [{self.addr}]")
                return True
            log.warning(f"Oczekiwano PONG, dostałem '{msg.get('type')}' od [{self.addr}]")
            return False
        except (asyncio.TimeoutError, Exception):
            return False

    # ── parsowanie i walidacja ────────────────────────────────────────────────

    async def _parse(self, data: bytes) -> tuple:
        """
        Waliduje i parsuje surowe bajty.
        Sprawdza kolejno: rozmiar → JSON → pola → rate limit → replay.
        Zwraca (msg, is_fatal) - is_fatal=True jeśli należy zamknąć połączenie.
        """
        # 1. rozmiar
        if len(data) > MAX_MSG:
            await self._send("ERROR", code="MSG_TOO_LARGE")
            return None, True

        # 2. JSON
        try:
            msg = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            await self._send("ERROR", code="PARSE_ERROR")
            return None, True

        # 3. wymagane pola (spec §2.1)
        missing = [f for f in REQUIRED_FIELDS if f not in msg]
        if missing:
            await self._send("ERROR", code="MISSING_FIELD", detail=str(missing))
            return None, False   # niekrytyczne

        # 4. rate limiting (per połączenie - blokuje flooding przed i po autoryzacji)
        if not self._msg_limiter.is_allowed("conn"):
            await self._send("ERROR", code="RATE_LIMITED")
            return None, False   # niekrytyczne - sesja trwa, ale ignorujemy tę wiadomość

        # 5. replay protection - sprawdzamy msg_id i timestamp
        msg_id    = msg.get("msg_id", "")
        timestamp = msg.get("timestamp", "")
        if not self._replay.is_valid(msg_id, timestamp):
            await self._send("ERROR", code="REPLAY_DETECTED")
            return None, True   # replay = krytyczne - zamykamy połączenie

        return msg, False

    async def _dispatch(self, msg: dict) -> bool:
        """Wywołuje właściwy handler. Zwraca False jeśli pętla powinna się zakończyć."""
        mtype   = msg.get("type", "")
        handler = self._dispatch_map.get(mtype, self._on_unknown)
        log.info(f"  [{self.addr}] -> {mtype}")
        return await handler(msg)

    # ── handlery typów wiadomości ─────────────────────────────────────────────
    # Każdy zwraca True (kontynuuj) lub False (zakończ połączenie)

    async def _on_hello(self, msg: dict) -> bool:
        # HELLO to tylko powitanie - spec nie wymaga odpowiedzi, czekamy na AUTH
        return True

    async def _on_auth(self, msg: dict) -> bool:
        username = msg.get("username", "")
        pw_hash  = msg.get("password_hash", "")

        # rate limiting: max 5 prób AUTH na minutę z tego IP
        ip = self.addr[0] if self.addr else "unknown"
        if not self._auth_limiter.is_allowed(ip):
            log.warning(f"AUTH rate limit przekroczony dla IP {ip}")
            await self._send("AUTH_FAIL", error="RATE_LIMITED")
            return True   # niekrytyczne - klient może spróbować później

        if self.db.verify_credentials(username, pw_hash):
            # tworzymy JWT zamiast zwykłego UUID - token zawiera weryfikowalne claims
            self.token = create_jwt(username)
            self.store.add_session(self.token, self.writer, username)
            log.info(f"AUTH_OK  -> '{username}'")
            await self._send("AUTH_OK", session_token=self.token, username=username)
        else:
            log.info(f"AUTH_FAIL -> '{username}'")
            await self._send("AUTH_FAIL", error="AUTH_FAILED")
        return True

    async def _on_queue_join(self, msg: dict) -> bool:
        tok = self._validate_token(msg)
        if tok is None:
            await self._send("ERROR", code="SESSION_EXPIRED")
            return False

        self.store.enqueue(tok)
        username = self.store.get_session(tok)["username"]
        log.info(f"Kolejka: '{username}' dołączył (czeka: {self.store.queue_size})")

        pair = self.store.try_match()
        if pair:
            await self._start_game(*pair)
        return True

    async def _start_game(self, t1: str, t2: str) -> None:
        """Inicjalizuje grę i wysyła GAME_START do obu graczy + startuje timer ruchu."""
        gid = self.store.create_game(t1, t2)
        u1  = self.store.get_session(t1)["username"]
        u2  = self.store.get_session(t2)["username"]
        log.info(f"Gra {gid}: '{u1}'(X) vs '{u2}'(O)")

        await self._send_to(t1, "GAME_START",
            game_id=gid, symbol="X", opponent=u2,
            first_turn=u1, session_token=t1)
        await self._send_to(t2, "GAME_START",
            game_id=gid, symbol="O", opponent=u1,
            first_turn=u1, session_token=t2)

        # X zaczyna - startujemy timer dla t1
        self._arm_move_timer(gid, t1)

    async def _on_move(self, msg: dict) -> bool:
        tok = self._validate_token(msg)
        if tok is None:
            await self._send("ERROR", code="SESSION_EXPIRED")
            return False

        sess = self.store.get_session(tok)
        gid  = sess.get("game_id")

        if not gid or not self.store.get_game(gid):
            await self._send("ERROR", code="NOT_IN_GAME")
            return True

        game = self.store.get_game(gid)

        if game["turn"] != tok:
            await self._send("MOVE_INVALID", error="NOT_YOUR_TURN")
            return True

        pos = msg.get("position")
        if not isinstance(pos, int) or not (0 <= pos <= 8) or game["board"][pos]:
            await self._send("MOVE_INVALID", error="INVALID_POSITION")
            return True

        # ruch prawidłowy
        game["board"][pos] = sess["symbol"]
        board = game["board"][:]
        other = self.store.get_opponent(tok, gid)

        winner = GameEngine.check_winner(board)
        if winner:
            await self._notify_result(tok, other, board, gid)
            self.store.end_game(gid)   # end_game anuluje timer ruchu
            log.info(f"Gra {gid}: '{sess['username']}' wygrywa!")
        elif GameEngine.is_draw(board):
            await self._notify_both(gid, "GAME_OVER", result="DRAW", board=board, game_id=gid)
            self.store.end_game(gid)
            log.info(f"Gra {gid}: remis")
        else:
            game["turn"] = other
            next_user = (
                self.store.get_session(other)["username"]
                if other and self.store.get_session(other)
                else "?"
            )
            await self._notify_both(gid, "MOVE_OK",
                board=board, next_turn=next_user,
                game_id=gid, position=pos)
            # przestawiamy timer na nowego gracza
            self._arm_move_timer(gid, other)
        return True

    async def _on_pong(self, msg: dict) -> bool:
        # PONG wysłany poza cyklem keepalive (np. klient wysłał PONG prewencyjnie)
        # ignorujemy - keepalive czyta PONG bezpośrednio z reader w _do_keepalive
        return True

    async def _on_bye(self, msg: dict) -> bool:
        await self._send("BYE_ACK")
        log.info(f"Graceful disconnect: {self.addr}")
        return False

    async def _on_unknown(self, msg: dict) -> bool:
        await self._send("ERROR", code="UNKNOWN_TYPE", detail=msg.get("type", "?"))
        return True

    # ── move timeout ──────────────────────────────────────────────────────────

    def _arm_move_timer(self, gid: str, player_tok: str) -> None:
        """Uruchamia (lub restartuje) timer 60s dla danego gracza."""
        task = asyncio.create_task(
            _move_timeout_task(self.store, gid, player_tok)
        )
        self.store.set_move_timer(gid, task)

    # ── wysyłanie ─────────────────────────────────────────────────────────────

    async def _notify_result(self, winner: str, loser: str | None,
                             board: list, gid: str) -> None:
        """Wysyła GAME_OVER z różnymi wynikami (WIN / LOSE) do obu graczy."""
        await self._send_to(winner, "GAME_OVER", result="WIN", board=board, game_id=gid)
        if loser and self.store.get_session(loser):
            await self._send_to(loser, "GAME_OVER", result="LOSE", board=board, game_id=gid)

    async def _notify_both(self, gid: str, msg_type: str, **kwargs) -> None:
        """Wysyła identyczną wiadomość do obu graczy (używane dla MOVE_OK i DRAW)."""
        game = self.store.get_game(gid)
        if not game:
            return
        payload = build_frame(msg_type, **kwargs)
        for tok in (game["p1"], game["p2"]):
            sess = self.store.get_session(tok)
            if sess:
                await _tcp_send(sess["writer"], payload)

    async def _send(self, msg_type: str, **kwargs) -> None:
        """Wysyła wiadomość do tego klienta."""
        await _tcp_send(self.writer, build_frame(msg_type, **kwargs))

    async def _send_to(self, token: str, msg_type: str, **kwargs) -> None:
        """Wysyła wiadomość do klienta identyfikowanego przez token sesji."""
        sess = self.store.get_session(token)
        if sess:
            await _tcp_send(sess["writer"], build_frame(msg_type, **kwargs))

    # ── pomocnicze ────────────────────────────────────────────────────────────

    def _validate_token(self, msg: dict) -> str | None:
        """
        Sprawdza token sesji z wiadomości:
          1. Czy jest w słowniku aktywnych sesji
          2. Czy podpis JWT jest prawidłowy (nie sfałszowany, nie wygasły)
        Wydzielone bo wzorzec powtarzał się w każdym handlerze (DRY).
        """
        tok = msg.get("session_token")
        if not tok or not self.store.get_session(tok):
            return None
        if not verify_jwt(tok):
            return None  # token wygasły lub sfałszowany
        return tok

    async def _cleanup(self) -> None:
        """
        Sprzątanie po rozłączeniu.
        Wykonuje się zawsze (blok finally), nawet po wyjątkach.
        Jeśli gracz był w grze, jego przeciwnik dostaje walkower.
        """
        if self.token and self.store.get_session(self.token):
            sess = self.store.get_session(self.token)
            gid  = sess.get("game_id")

            if gid and self.store.get_game(gid):
                game  = self.store.get_game(gid)
                other = self.store.get_opponent(self.token, gid)
                if other and self.store.get_session(other):
                    await self._send_to(other, "GAME_OVER",
                        result="WIN", board=game["board"],
                        reason="opponent_disconnected")
                self.store.end_game(gid)

            self.store.remove_session(self.token)

        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass

        log.info(f"Zamknięto połączenie: {self.addr}")


# ── funkcje pomocnicze na poziomie modułu ────────────────────────────────────

async def _tcp_send(writer, payload: bytes) -> None:
    """Najniższy poziom wysyłania - wrzuca bajty do TCP i spłukuje."""
    try:
        writer.write(payload)
        await writer.drain()
    except Exception:
        pass  # klient mógł się rozłączyć


async def _move_timeout_task(store: SessionStore, gid: str, player_tok: str) -> None:
    """
    Background task - wypala się po MOVE_TIMEOUT sekundach braku ruchu.
    Jeśli gracz nie zagrał na czas, traci grę przez walkower.

    Ważne: przed wywołaniem store.end_game() zerujemy move_timer w grze
    żeby uniknąć self-cancellation (end_game anuluje aktywne timery).
    """
    try:
        await asyncio.sleep(MOVE_TIMEOUT)
    except asyncio.CancelledError:
        return  # timer anulowany - gra zakończyła się normalnie

    game = store.get_game(gid)
    if not game or game.get("turn") != player_tok:
        return  # gra już zakończona lub tura się zmieniła

    log.info(f"Gra {gid}: timeout ruchu gracza {player_tok[:8]}...")

    # zerujemy timer ZANIM wywołamy end_game - żeby end_game nie próbował nas anulować
    game["move_timer"] = None

    other = store.get_opponent(player_tok, gid)
    board = game["board"][:]
    store.end_game(gid)

    # powiadamiamy obu graczy
    loser_sess  = store.get_session(player_tok)
    winner_sess = store.get_session(other) if other else None

    if loser_sess:
        await _tcp_send(loser_sess["writer"], build_frame(
            "GAME_OVER", result="LOSE", board=board,
            game_id=gid, reason="move_timeout"))
    if winner_sess:
        await _tcp_send(winner_sess["writer"], build_frame(
            "GAME_OVER", result="WIN", board=board,
            game_id=gid, reason="opponent_timeout"))
