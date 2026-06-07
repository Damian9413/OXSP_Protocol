"""
Główna logika klienta OXSP.

OXSPClient zarządza połączeniem TLS/TCP, sesją i przepływem gry.
Deleguje wyświetlanie do Display (SRP) i budowanie ramek do build_frame (DRY).

Trochę o asyncio:
  Musimy JEDNOCZEŚNIE czekać na wiadomości z serwera I czekać na input użytkownika.
  Rozwiązanie: dwa osobne taski asyncio + asyncio.Queue do komunikacji między nimi.
  Background task czyta serwer → wkłada do kolejki.
  Główna pętla wyjmuje z kolejki → przetwarza.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
import sys

from client.config   import HOST, PORT
from client.display  import Display
from shared.protocol import build_frame


# ── pomocnicze ────────────────────────────────────────────────────────────────

async def get_input(prompt: str = "") -> str:
    """
    Czyta linię ze stdin bez blokowania event loop asyncio.
    run_in_executor przenosi blokujące stdin.readline() do osobnego wątku.
    """
    print(prompt, end="", flush=True)
    loop = asyncio.get_event_loop()
    return (await loop.run_in_executor(None, sys.stdin.readline)).strip()


def _sha256(text: str) -> str:
    """Hash SHA-256 hasła - nigdy nie wysyłamy plaintext."""
    return hashlib.sha256(text.encode()).hexdigest()


def _build_ssl_context() -> ssl.SSLContext:
    """
    Tworzy SSL kontekst klienta.
    check_hostname=False i CERT_NONE bo używamy self-signed certyfikatu.
    Na produkcji należy weryfikować certyfikat serwera!
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE  # self-signed - wyłączamy weryfikację CA
    return ctx


# ── główna klasa klienta ──────────────────────────────────────────────────────

class OXSPClient:
    """
    Zarządza połączeniem z serwerem i przepływem sesji od logowania do końca gry.

    Stan sesji (token, symbol, game_id) trzymamy jako atrybuty instancji
    zamiast globalnych zmiennych (DRY, brak side effects).
    """

    # kody błędów po których nie ma sensu kontynuować sesji
    FATAL_ERRORS = frozenset({
        "PARSE_ERROR", "MSG_TOO_LARGE", "SESSION_EXPIRED", "REPLAY_DETECTED"
    })

    def __init__(self, host: str = HOST, port: int = PORT):
        self.host            = host
        self.port            = port
        self._session_token: str | None = None   # ustawiany po AUTH_OK
        self.username:       str | None = None
        self.symbol:         str | None = None   # 'X' lub 'O'
        self.game_id:        str | None = None

    async def run(self) -> None:
        """Punkt wejścia - przeprowadza przez cały cykl od logowania do końca gry."""
        Display.welcome()

        self.username = await get_input("Login: ")
        password      = await get_input("Hasło: ")

        ssl_ctx = _build_ssl_context()

        try:
            reader, writer = await asyncio.open_connection(
                self.host, self.port, ssl=ssl_ctx
            )
        except ConnectionRefusedError:
            print(f"\n[BŁĄD] Nie można połączyć z {self.host}:{self.port}")
            print("Upewnij się że serwer jest uruchomiony:  python server.py")
            return
        except ssl.SSLError as e:
            print(f"\n[BŁĄD TLS] {e}")
            return

        print("\nPodłączono (TLS)!\n")

        # HELLO + AUTH wysyłamy razem na starcie
        writer.write(self._build("HELLO", proto_version="1.0", client_name="OXSP-CLI"))
        writer.write(self._build("AUTH", username=self.username,
                                 password_hash=_sha256(password)))
        await writer.drain()

        await self._game_loop(reader, writer)

    async def _game_loop(self, reader, writer) -> None:
        """
        Główna pętla klienta.
        Background task czyta z serwera i wkłada wiadomości do kolejki.
        Główna pętla wyjmuje z kolejki i obsługuje każdą wiadomość.
        None w kolejce = sygnał że serwer zamknął połączenie.
        """
        queue = asyncio.Queue()
        reader_task = asyncio.create_task(
            self._server_reader(reader, writer, queue)
        )

        try:
            while True:
                msg = await queue.get()
                if msg is None:
                    print("\nSerwer zamknął połączenie.")
                    break

                done = await self._handle(msg, writer)
                if done:
                    break
        finally:
            reader_task.cancel()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _server_reader(self, reader, writer, queue: asyncio.Queue) -> None:
        """
        Background task: czyta wiadomości z serwera i wkłada do kolejki.
        PING obsługuje sam (auto-PONG) żeby nie angażować głównej pętli w keepalive.
        """
        while True:
            try:
                data = await reader.readline()
                if not data:
                    await queue.put(None)
                    return
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "PING":
                    # auto-odpowiedź na PING - nie wkładamy do kolejki
                    writer.write(self._build("PONG"))
                    await writer.drain()
                else:
                    await queue.put(msg)
            except json.JSONDecodeError:
                pass   # śmieci z sieci - ignorujemy
            except Exception:
                await queue.put(None)
                return

    async def _handle(self, msg: dict, writer) -> bool:
        """
        Dispatchuje wiadomość do właściwego handlera.
        Zwraca True jeśli pętla powinna się zakończyć.
        """
        handlers = {
            "AUTH_OK":      self._on_auth_ok,
            "AUTH_FAIL":    self._on_auth_fail,
            "GAME_START":   self._on_game_start,
            "MOVE_OK":      self._on_move_ok,
            "MOVE_INVALID": self._on_move_invalid,
            "GAME_OVER":    self._on_game_over,
            "BYE_ACK":      self._on_bye_ack,
            "ERROR":        self._on_error,
        }
        handler = handlers.get(msg.get("type"))
        if handler:
            return await handler(msg, writer)
        return False   # nieznany typ - ignorujemy

    # ── handlery wiadomości od serwera ────────────────────────────────────────

    async def _on_auth_ok(self, msg: dict, writer) -> bool:
        self._session_token = msg["session_token"]
        print(f"[OK] Zalogowano jako '{self.username}'")
        print("Szukam przeciwnika... (czekanie na 2. gracza)\n")
        writer.write(self._build("QUEUE_JOIN"))
        await writer.drain()
        return False

    async def _on_auth_fail(self, msg: dict, writer) -> bool:
        err = msg.get("error", "?")
        if err == "RATE_LIMITED":
            print("[BŁĄD] Zbyt wiele prób logowania. Poczekaj chwilę i spróbuj ponownie.")
        else:
            print("[BŁĄD] Nieprawidłowy login lub hasło.")
        return True

    async def _on_game_start(self, msg: dict, writer) -> bool:
        self.symbol  = msg["symbol"]
        self.game_id = msg["game_id"]
        opponent     = msg.get("opponent", "?")
        first        = msg.get("first_turn", "")

        Display.game_start(self.symbol, opponent, self.game_id)
        Display.board([""] * 9)

        if first == self.username:
            await self._prompt_move(writer)
        else:
            print(f"Czekaj - tura gracza {opponent}... (masz 60s gdy przyjdzie twoja kolej)")
        return False

    async def _on_move_ok(self, msg: dict, writer) -> bool:
        board     = msg.get("board", [""] * 9)
        next_turn = msg.get("next_turn", "")
        Display.board(board)

        if next_turn == self.username:
            await self._prompt_move(writer)
        else:
            print(f"Czekaj - tura gracza {next_turn}...")
        return False

    async def _on_move_invalid(self, msg: dict, writer) -> bool:
        err = msg.get("error", "?")
        if err == "NOT_YOUR_TURN":
            print("[!] To nie twoja tura - poczekaj.")
        else:
            print(f"[!] Nieprawidłowy ruch ({err}). Spróbuj ponownie.")
            await self._prompt_move(writer)
        return False

    async def _on_game_over(self, msg: dict, writer) -> bool:
        Display.board(msg.get("board", [""] * 9))
        Display.game_over(msg.get("result", "?"), msg.get("reason", ""))
        writer.write(self._build("BYE"))
        await writer.drain()
        return False

    async def _on_bye_ack(self, msg: dict, writer) -> bool:
        print("\nDo zobaczenia!")
        return True

    async def _on_error(self, msg: dict, writer) -> bool:
        code   = msg.get("code", "?")
        detail = msg.get("detail", "")
        print(f"\n[BŁĄD SERWERA] {code}{' - ' + detail if detail else ''}")
        return code in self.FATAL_ERRORS

    # ── pomocnicze ────────────────────────────────────────────────────────────

    async def _prompt_move(self, writer) -> None:
        """
        Pyta o pole i wysyła MOVE. Pętla aż do wpisania poprawnej liczby 0-8.
        Mamy 60s na ruch zanim serwer przyzna walkower!
        """
        while True:
            pos_str = await get_input(f"[{self.symbol}] Twoja tura! Pole (0-8): ")
            try:
                pos = int(pos_str)
                if 0 <= pos <= 8:
                    writer.write(self._build("MOVE", position=pos, game_id=self.game_id))
                    await writer.drain()
                    return
                print("[!] Wpisz liczbę od 0 do 8.")
            except ValueError:
                print(f"[!] '{pos_str}' to nie jest liczba.")

    def _build(self, msg_type: str, **kwargs) -> bytes:
        """Buduje ramkę OXSP z aktualnym tokenem sesji."""
        return build_frame(msg_type, session_token=self._session_token, **kwargs)
