#!/usr/bin/env python3
"""
Punkt wejścia serwera OXSP.

Uruchomienie:
    python server.py

Ten plik celowo jest krótki - cała logika jest w pakiecie server/.
Dzięki temu ten plik działa jak prosty "przycisk start" i nie miesza
konfiguracji startu z logiką biznesową.
"""

import asyncio

from server.core import OXSPServer


if __name__ == "__main__":
    try:
        asyncio.run(OXSPServer().start())
    except KeyboardInterrupt:
        print("\nSerwer zatrzymany.")
