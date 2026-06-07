#!/usr/bin/env python3
"""
Skrypt do generowania self-signed certyfikatu TLS dla serwera OXSP.

Uruchom RAZ przed pierwszym startem serwera:
    python setup_tls.py

Tworzy:
    tls/cert.pem  - certyfikat publiczny (serwer go wysyła klientom)
    tls/key.pem   - klucz prywatny (TYLKO dla serwera, nie udostępniać!)

Uwaga: self-signed certyfikat = klient wyłącza weryfikację CA.
Na produkcji należy użyć certyfikatu od zaufanego CA (np. Let's Encrypt).
"""

import os
import subprocess
import sys


def generate_cert() -> None:
    os.makedirs("tls", exist_ok=True)

    cert = "tls/cert.pem"
    key  = "tls/key.pem"

    if os.path.exists(cert) and os.path.exists(key):
        print(f"Certyfikat już istnieje: {cert}")
        print("Usuń pliki tls/ jeśli chcesz wygenerować nowy.")
        return

    cmd = [
        "openssl", "req", "-x509",
        "-newkey", "rsa:2048",
        "-keyout", key,
        "-out",    cert,
        "-days",   "365",
        "-nodes",
        "-subj",   "/CN=localhost/O=OXSP PUS/C=PL",
    ]

    print("Generuję self-signed certyfikat TLS (RSA 2048, 365 dni)...")
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"OK! Certyfikat: {cert}")
        print(f"OK! Klucz:      {key}")
        print("\nTeraz możesz uruchomić serwer: python server.py")
    except FileNotFoundError:
        print("[BŁĄD] Nie znaleziono 'openssl'. Zainstaluj OpenSSL i spróbuj ponownie.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"[BŁĄD] openssl: {e.stderr.decode()}")
        sys.exit(1)


if __name__ == "__main__":
    generate_cert()
