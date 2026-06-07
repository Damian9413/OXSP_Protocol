"""
Ochrona przed replay attacks.

Replay attack = atakujący przechwytuje prawidłową wiadomość (np. MOVE)
i wysyła ją ponownie żeby powtórzyć akcję.

Chronimy się przez:
  1. Sprawdzenie czy timestamp jest w oknie ±30s od czasu serwera
     (stara wiadomość / z przyszłości = odrzucamy)
  2. Zapamiętanie już widzianych msg_id i odrzucanie duplikatów
"""
from __future__ import annotations

import time
from collections import OrderedDict
from datetime import datetime, timezone

TIMESTAMP_WINDOW = 30    # sekund - dopuszczalne odchylenie czasu klienta od serwera
MAX_SEEN_IDS     = 5000  # max msg_id w pamięci (żeby nie wyciekać RAM przy długich sesjach)


class ReplayGuard:
    """
    Weryfikuje że każda wiadomość jest "świeża" i nie była już przetworzona.

    Jedna instancja na połączenie TCP (w ClientHandler).
    Chroni przed replay w obrębie jednej sesji.
    Cross-session replay jest blokowany przez weryfikację JWT i token sesji.
    """

    def __init__(self):
        # OrderedDict żeby móc usuwać najstarsze wpisy (FIFO)
        self._seen: OrderedDict = OrderedDict()

    def is_valid(self, msg_id: str, timestamp_str: str) -> bool:
        """
        Sprawdza czy wiadomość jest prawidłowa (nie replay).
        Zwraca True jeśli OK, False jeśli to atak powtórkowy lub stary timestamp.
        """
        # 1. sprawdzamy czy timestamp mieści się w oknie ±30s
        if not self._timestamp_ok(timestamp_str):
            return False

        # 2. sprawdzamy czy msg_id był już widziany
        if msg_id in self._seen:
            return False  # klasyczny replay - ta wiadomość już była przetworzona

        # rejestrujemy nowy msg_id z czasem odbioru
        self._seen[msg_id] = time.monotonic()

        # sprzątamy pamięć jeśli za dużo wpisów - usuwamy najstarszy
        if len(self._seen) > MAX_SEEN_IDS:
            self._seen.popitem(last=False)

        return True

    @staticmethod
    def _timestamp_ok(timestamp_str: str) -> bool:
        """Sprawdza czy timestamp klienta jest w oknie ±TIMESTAMP_WINDOW sekund."""
        try:
            ts  = datetime.fromisoformat(timestamp_str)
            now = datetime.now(timezone.utc)
            if ts.tzinfo is None:
                # klient zapomniał o timezone - zakładamy UTC
                ts = ts.replace(tzinfo=timezone.utc)
            diff = abs((now - ts).total_seconds())
            return diff <= TIMESTAMP_WINDOW
        except Exception:
            return False  # nieparsywalny timestamp = odrzucamy
