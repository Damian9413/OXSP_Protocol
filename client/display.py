"""
Wyświetlanie UI w terminalu.

Single Responsibility: ta klasa TYLKO wypisuje rzeczy na ekran.
Zero logiki biznesowej, zero operacji sieciowych.

Dzięki temu jeśli kiedyś będziemy robić GUI (np. tkinter albo webowy),
wymieniamy tylko tę klasę - reszta kodu (OXSPClient) zostaje bez zmian.
"""

from client.config import HOST, PORT


class Display:
    """Wszystkie komunikaty wyświetlane użytkownikowi w jednym miejscu."""

    @staticmethod
    def welcome() -> None:
        print("=" * 36)
        print("   OXSP - Kółko i Krzyżyk (CLI)  ")
        print("=" * 36)
        print(f"Serwer: {HOST}:{PORT}\n")

    @staticmethod
    def board(board: list) -> None:
        """
        Rysuje planszę 3x3 w terminalu.
        Puste pola pokazuje jako '.' żeby były widoczne.
        W nawiasach podajemy indeksy pól (0-8) jako podpowiedź dla gracza.
        """
        sym = lambda c: c if c else "."
        b = [sym(c) for c in board]
        print(f"\n  {b[0]} | {b[1]} | {b[2]}   [0|1|2]")
        print(f"  --+---+--")
        print(f"  {b[3]} | {b[4]} | {b[5]}   [3|4|5]")
        print(f"  --+---+--")
        print(f"  {b[6]} | {b[7]} | {b[8]}   [6|7|8]")
        print()

    @staticmethod
    def game_start(symbol: str, opponent: str, game_id: str) -> None:
        print("\n" + "=" * 36)
        print(f"  NOWA GRA!  Ty: {symbol}   Rywal: {opponent}")
        print(f"  ID gry: {game_id}")
        print("=" * 36)

    @staticmethod
    def game_over(result: str, reason: str = "") -> None:
        """Wyświetla komunikat końca gry. Słownik zamiast if/elif dla czytelności."""
        messages = {
            "WIN":  "  *** WYGRAŁEŚ! Gratulacje! ***",
            "LOSE": "  Przegrałeś. Następnym razem!",
            "DRAW": "  Remis - nikt nie wygrał!",
        }
        print("-" * 36)
        print(messages.get(result, f"  Koniec gry: {result}"))
        if reason:
            print(f"  Powód: {reason}")
        print("-" * 36)
