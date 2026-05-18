#!/usr/bin/env python3
"""
Cria o usuário de teste inicial via API REST.
Roda após o backend estar saudável.
"""
import sys
import httpx

API_URL = "http://localhost:8000"
EMAIL = "admin@viabilidade.com"
PASSWORD = "admin123"
FULL_NAME = "Admin"


def main():
    try:
        resp = httpx.post(
            f"{API_URL}/api/auth/register",
            json={"email": EMAIL, "password": PASSWORD, "full_name": FULL_NAME},
            timeout=10,
        )
        if resp.status_code == 201:
            print(f"Usuário criado: {EMAIL}")
        elif resp.status_code == 409:
            print(f"Usuário já existe: {EMAIL}")
        else:
            print(f"Aviso: {resp.status_code} — {resp.text}", file=sys.stderr)
            sys.exit(1)
    except httpx.ConnectError:
        print("Backend não acessível em localhost:8000", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
