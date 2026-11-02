"""Utilidades de línea de comandos para provisionar credenciales (sin guardar secretos).

Uso:
    uv run python -m agrosense_api.cli hash-secret          # pide el secreto sin eco
    uv run python -m agrosense_api.cli new-buyer-key        # genera api_key + hash Argon2
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys

from agrosense_api.core.security import hash_password


def _hash_secret(_args: argparse.Namespace) -> int:
    if sys.stdin.isatty():
        secret = getpass.getpass("Secreto a hashear: ")
    else:
        secret = sys.stdin.readline().rstrip("\n")
    if not secret:
        print("El secreto no puede estar vacío.", file=sys.stderr)
        return 1
    print(hash_password(secret))
    return 0


def _new_buyer_key(_args: argparse.Namespace) -> int:
    api_key = secrets.token_urlsafe(32)
    print(f"api_key (entregar al comprador por un canal seguro, no se vuelve a mostrar): {api_key}")
    print(f"api_key_hash (guardar en international_buyers.api_key_hash): {hash_password(api_key)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agrosense-cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hash-secret", help="Genera un hash Argon2 de un secreto (stdin o prompt)")
    sub.add_parser("new-buyer-key", help="Genera un api_key de comprador y su hash Argon2")
    args = parser.parse_args(argv)
    handlers = {"hash-secret": _hash_secret, "new-buyer-key": _new_buyer_key}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
