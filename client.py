#!/usr/bin/env python3
"""
Punkt wejścia klienta OXSP.

Uruchomienie:
    python client.py

Tak samo jak server.py - ten plik to tylko "przycisk start".
Cała logika klienta jest w pakiecie client/.
"""

import asyncio

from client.core import OXSPClient


if __name__ == "__main__":
    try:
        asyncio.run(OXSPClient().run())
    except KeyboardInterrupt:
        print("\nRozłączono.")
