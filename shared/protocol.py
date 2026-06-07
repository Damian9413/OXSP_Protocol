"""
Wspólne elementy protokołu OXSP.

Ten moduł jest używany przez ZARÓWNO serwer jak i klienta - dlatego
jest w osobnym pakiecie 'shared'. Dzięki temu nie duplikujemy logiki
budowania ramek w dwóch miejscach (zasada DRY).

Format ramki OXSP (wg specyfikacji §2.1):
  - obiekt JSON, zakończony znakiem nowej linii '\\n'
  - separator '\\n' pozwala odróżnić kolejne wiadomości w strumieniu TCP
  - pola obowiązkowe: type, msg_id, timestamp, session_token
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone


def build_frame(msg_type: str, session_token: str | None = None, **kwargs) -> bytes:
    """
    Buduje jedną ramkę protokołu OXSP i zwraca bajty gotowe do wysłania przez TCP.

    Dlaczego bytes a nie str? Bo TCP operuje na surowych bajtach,
    a asyncio writer.write() też przyjmuje bytes.

    Parametry:
        msg_type      - typ wiadomości np. 'AUTH', 'MOVE', 'GAME_OVER'
        session_token - token sesji (None przed zalogowaniem / dla wiadomości systemowych)
        **kwargs      - dodatkowe pola specyficzne dla danego typu wiadomości

    Przykład użycia:
        build_frame('MOVE_OK', session_token=tok, board=[...], next_turn='alice')
    """
    frame = {
        "type":          msg_type,
        "msg_id":        str(uuid.uuid4()),              # UUID4 - losowy, unikalny identyfikator
        "timestamp":     datetime.now(timezone.utc).isoformat(),  # czas UTC, format ISO8601
        "session_token": session_token,
    }
    # nadpisujemy / dopisujemy pola specyficzne dla danego typu
    frame.update(kwargs)

    # json.dumps -> string, + '\n' -> separator ramek, .encode() -> bytes
    return (json.dumps(frame) + "\n").encode()
