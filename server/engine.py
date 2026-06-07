"""
Logika gry Kółko i Krzyżyk.

GameEngine jest całkowicie niezależny od sieci, sesji i bazy danych.
To czysta logika domenowa - można ją testować jednostkowo bez żadnego serwera.
Zasada SRP: jeśli zasady gry się zmienią (np. plansza 4x4), zmieniamy tylko ten plik.
"""
from __future__ import annotations


class GameEngine:
    """
    Sprawdza stan planszy: zwycięzcę i remis.

    Plansza to lista 9 elementów indeksowanych 0-8:
      0 | 1 | 2
      ---------
      3 | 4 | 5
      ---------
      6 | 7 | 8

    Puste pole = pusty string ''.
    """

    # wszystkie możliwe linie wygranej - stała klasy żeby nie tworzyć listy przy każdym sprawdzeniu
    WIN_LINES: list[tuple[int, int, int]] = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),   # rzędy poziome
        (0, 3, 6), (1, 4, 7), (2, 5, 8),   # kolumny pionowe
        (0, 4, 8), (2, 4, 6),               # przekątne
    ]

    @staticmethod
    def check_winner(board: list) -> str | None:
        """Zwraca 'X' albo 'O' jeśli ktoś wygrał, None jeśli gra trwa lub remis."""
        for a, b, c in GameEngine.WIN_LINES:
            # wszystkie trzy pola muszą być takie same i niepuste
            if board[a] and board[a] == board[b] == board[c]:
                return board[a]
        return None

    @staticmethod
    def is_draw(board: list) -> bool:
        """
        Remis = pełna plansza i brak zwycięzcy.
        check_winner() musi być wywołany wcześniej żeby to miało sens.
        """
        return "" not in board
