"""Manual auth probe: do the GL.iNet challenge/login by hand.

Bypasses pyglinet's abstractions to expose every intermediate value so we
can pinpoint where the hash mismatch happens.

Usage:
    .venv/bin/python -m scripts.probe_login_manual
"""

from __future__ import annotations

import getpass
import hashlib
import json
import sys

import httpx
from passlib.hash import md5_crypt, sha256_crypt, sha512_crypt

URL = "https://192.168.8.1/rpc"

ALG_MAP = {
    "1": md5_crypt,
    "5": sha256_crypt,
    "6": sha512_crypt,
    1: md5_crypt,
    5: sha256_crypt,
    6: sha512_crypt,
}

HASH_MAP = {
    "md5": hashlib.md5,
    "sha256": hashlib.sha256,
    "sha512": hashlib.sha512,
}


def rpc(client: httpx.Client, method: str, params: dict) -> dict:
    resp = client.post(
        URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=10.0,
    )
    return resp.json()


def main() -> int:
    username = input("username [root]: ").strip() or "root"
    password = getpass.getpass("password (no echo): ")

    with httpx.Client(verify=False) as client:
        print(f"\n[1] POST challenge for {username!r}")
        challenge_resp = rpc(client, "challenge", {"username": username})
        print(json.dumps(challenge_resp, indent=2))

        if "error" in challenge_resp:
            print("\n✗ challenge failed", file=sys.stderr)
            return 1

        chal = challenge_resp["result"]
        salt = chal["salt"]
        nonce = chal["nonce"]
        alg = chal["alg"]
        hash_method = chal.get("hash-method", "md5")

        print(f"\n  salt        = {salt}")
        print(f"  nonce       = {nonce}")
        print(f"  alg         = {alg} (type={type(alg).__name__})")
        print(f"  hash-method = {hash_method}")

        # --- Step 1: unix passwd hash with default rounds (what pyglinet does)
        crypt_func = ALG_MAP.get(alg) or ALG_MAP.get(str(alg))
        if crypt_func is None:
            print(f"\n✗ unsupported alg {alg}", file=sys.stderr)
            return 1
        unix_hash = crypt_func.hash(password, salt=salt, rounds=5000)
        print(f"\n[2] unix_hash (rounds=5000) = {unix_hash}")

        hash_func = HASH_MAP.get(hash_method)
        if hash_func is None:
            print(f"\n✗ unsupported hash-method {hash_method}", file=sys.stderr)
            return 1

        login_payload = f"{username}:{unix_hash}:{nonce}"
        login_hash = hash_func(login_payload.encode()).hexdigest()
        print(f"  login_hash = {login_hash}")

        # Need a fresh challenge (the previous nonce expires immediately on use)
        challenge_resp = rpc(client, "challenge", {"username": username})
        chal = challenge_resp["result"]
        salt = chal["salt"]
        nonce = chal["nonce"]
        unix_hash = crypt_func.hash(password, salt=salt, rounds=5000)
        login_hash = hash_func(f"{username}:{unix_hash}:{nonce}".encode()).hexdigest()

        print("\n[3] POST login")
        login_resp = rpc(client, "login", {"username": username, "hash": login_hash})
        print(json.dumps(login_resp, indent=2))

        if "error" in login_resp:
            print(
                "\n✗ login rejected. The unix_hash above is what we computed."
                "\n  If you have shell access on the Slate, compare with /etc/shadow:"
                "\n    cat /etc/shadow | grep ^root"
                "\n  If the $rounds= portion differs, pyglinet's 5000 default is wrong.",
                file=sys.stderr,
            )
            return 1

        print("\n✓ login OK")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
