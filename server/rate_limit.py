"""
Rate limiting - ograniczanie liczby wiadomości na jednostkę czasu.

Implementujemy sliding window (przesuwane okno) co jest dokładniejsze
niż fixed window (stałe okno) - nie ma efektu "podwójnego limitu" na granicy okna.

Spec OXSP:
  - 60 wiadomości/min na sesję (blokuje flooding)
  - 5 prób AUTH/min na IP (blokuje brute-force haseł)
"""
from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    """
    Sliding window rate limiter.

    Dla każdego klucza (np. token sesji lub IP) przechowujemy
    kolejkę timestampów ostatnich requestów.
    Przy każdym sprawdzeniu wyrzucamy stare timestampy i liczymy ile zostało.
    """

    def __init__(self, max_count: int, window_seconds: float = 60.0):
        self.max_count = max_count
        self.window    = window_seconds
        # defaultdict(deque) = automatycznie tworzy pusty deque dla nowego klucza
        self._buckets: dict = defaultdict(deque)

    def is_allowed(self, key: str) -> bool:
        """
        Sprawdza czy dla danego klucza nie przekroczono limitu.
        Zwraca True jeśli request jest dozwolony, False jeśli rate-limited.
        Wywołanie tej metody ZAWSZE rejestruje request (jeśli dozwolony).
        """
        now    = time.monotonic()
        bucket = self._buckets[key]

        # wyrzucamy timestampy spoza aktualnego okna czasowego
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()

        if len(bucket) >= self.max_count:
            return False  # limit przekroczony

        bucket.append(now)
        return True

    def cleanup(self) -> None:
        """
        Usuwa nieaktywne klucze ze słownika.
        Warto wywoływać co jakiś czas żeby słownik nie rósł w nieskończoność.
        W naszym przypadku handlery mają krótki czas życia więc nie jest krytyczne.
        """
        now    = time.monotonic()
        stale  = [k for k, q in self._buckets.items()
                  if not q or now - q[-1] > self.window]
        for k in stale:
            del self._buckets[k]
