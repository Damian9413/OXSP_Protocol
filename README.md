# OXSP – Kółko i Krzyżyk przez sieć

Projekt zaliczeniowy z przedmiotu **Projektowanie Usług Sieciowych** (semestr 6).  
Autorzy: **Damian Skiba**, **Piotr Sarnecki**

Zaimplementowaliśmy własny protokół sieciowy **OXSP** (*OX Secure Protocol*) do gry w kółko i krzyżyk przez TCP. Całość w Pythonie, bez zewnętrznych bibliotek.

---

## Jak uruchomić

Wymagany Python **3.9+**. Żadnych `pip install` – używamy tylko standardowej biblioteki.

```bash
# 1. Wygeneruj certyfikat TLS (tylko raz, już to zrobiliśmy)
python setup_tls.py

# 2. Terminal 1 – serwer
python server.py

# 3. Terminal 2 – gracz 1
python client.py

# 4. Terminal 3 – gracz 2
python client.py
```

Konta testowe: `damian / damian` i `piotrek / piotrek`

---

## Struktura projektu

```
.
├── server.py            # uruchamia serwer (5 linii – tylko "przycisk start")
├── client.py            # uruchamia klienta (5 linii)
├── setup_tls.py         # generuje self-signed certyfikat TLS
│
├── shared/
│   └── protocol.py      # build_frame() – buduje ramki JSON wspólnie dla serwera i klienta
│
├── server/
│   ├── config.py        # stałe: HOST, PORT, timeouty, klucz JWT, ścieżki TLS
│   ├── auth.py          # JWT – tworzenie i weryfikacja tokenów (HMAC-SHA256)
│   ├── replay.py        # ochrona przed replay attacks (msg_id + timestamp ±30s)
│   ├── rate_limit.py    # rate limiting – sliding window
│   ├── database.py      # warstwa SQLite – konta użytkowników
│   ├── engine.py        # logika gry – sprawdzanie wygranej i remisu
│   ├── session.py       # stan serwera w RAM – sesje, kolejka, gry
│   ├── handler.py       # obsługa jednego połączenia TCP
│   └── core.py          # OXSPServer – start, TLS, dependency injection
│
├── client/
│   ├── config.py        # HOST i PORT klienta
│   ├── display.py       # wszystkie print() w jednym miejscu
│   └── core.py          # OXSPClient – logika klienta, asyncio
│
└── tls/
    ├── cert.pem         # certyfikat serwera
    └── key.pem          # klucz prywatny (nie udostępniać)
```

---

## Protokół OXSP

Komunikacja przez **TCP + TLS**, wiadomości w formacie **JSON** zakończone `\n`.

### Przepływ sesji

```
Klient                          Serwer
  |                               |
  |------ HELLO ----------------> |
  |------ AUTH (hash SHA-256) --> |
  |<----- AUTH_OK (JWT token) --- |
  |                               |
  |------ QUEUE_JOIN -----------> |   (czekamy na drugiego gracza)
  |<----- GAME_START ------------ |   (symbol X lub O, kto zaczyna)
  |                               |
  |------ MOVE (pozycja 0-8) ---> |
  |<----- MOVE_OK (plansza) ----- |   (broadcast do obu graczy)
  |         ...                   |
  |<----- GAME_OVER (wynik) ----- |
  |                               |
  |------ BYE -----------------> |
  |<----- BYE_ACK -------------- |
```

### Wszystkie typy wiadomości

| Typ | Kierunek | Opis |
|---|---|---|
| `HELLO` | K→S | Powitanie, wersja protokołu |
| `AUTH` | K→S | Login + SHA-256 hasła |
| `AUTH_OK` | S→K | Token JWT sesji |
| `AUTH_FAIL` | S→K | Złe dane lub rate limit |
| `QUEUE_JOIN` | K→S | Gracz chce zagrać |
| `GAME_START` | S→K | Gra znaleziona, symbol i kto zaczyna |
| `MOVE` | K→S | Ruch gracza (pole 0-8) |
| `MOVE_OK` | S→K | Ruch zaakceptowany, nowa plansza |
| `MOVE_INVALID` | S→K | Złe pole lub nie twoja tura |
| `GAME_OVER` | S→K | Koniec gry (WIN / LOSE / DRAW) |
| `PING` | S→K | Keep-alive po 30s bezczynności |
| `PONG` | K→S | Odpowiedź na PING |
| `BYE` | K→S | Gracz się rozłącza |
| `BYE_ACK` | S→K | Potwierdzenie rozłączenia |
| `ERROR` | S→K | Błąd protokołu |

### Format ramki

Każda wiadomość to obiekt JSON z polami obowiązkowymi:

```json
{
  "type":          "MOVE",
  "msg_id":        "550e8400-e29b-41d4-a716-446655440000",
  "timestamp":     "2026-06-07T14:30:00.000000+00:00",
  "session_token": "eyJhbGci...",
  "position":      4
}
```

---

## Zaimplementowane mechanizmy bezpieczeństwa

| Mechanizm | Szczegóły |
|---|---|
| **TLS** | Self-signed RSA 2048, szyfrowanie całego ruchu |
| **SHA-256** | Hasła nigdy nie są przesyłane ani przechowywane jako plaintext |
| **JWT (HMAC-SHA256)** | Tokeny sesji z expiry 1h, nie da się sfałszować bez klucza |
| **Replay protection** | Każde `msg_id` zapamiętywane; timestamp musi być w oknie ±30s |
| **Rate limiting** | 60 wiadomości/min na sesję; 5 prób AUTH/min na IP |
| **PING/PONG keepalive** | Serwer wysyła PING po 30s bezczynności; brak PONG w 10s = rozłączenie |
| **Move timeout** | 60 sekund na ruch – po przekroczeniu walkower |

---

## Zasady projektowe

Kod pisaliśmy starając się stosować **SOLID**, **KISS** i **DRY**:

- **Single Responsibility** – `engine.py` tylko liczy wygraną, `database.py` tylko SQL, `display.py` tylko wyświetla
- **Open/Closed** – nowy typ wiadomości = nowa metoda + jeden wpis w słowniku; reszta kodu nienaruszona
- **Dependency Inversion** – `ClientHandler` dostaje zależności przez konstruktor, nie tworzy ich sam
- **DRY** – `build_frame()` w `shared/`, `_validate_token()` wydzielony zamiast copy-paste w każdym handlerze
- **KISS** – entry pointy `server.py` i `client.py` mają po 5 linii; każda metoda robi jedną rzecz
