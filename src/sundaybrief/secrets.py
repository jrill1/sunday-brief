"""Secret loading, ordered by how reliably it survives a launchd run.

1. Process environment (populated from a chmod-600 `.env` by run.py).
2. macOS Keychain via the `security` CLI, only if SUNDAYBRIEF_USE_KEYCHAIN=1.

The env-file path is the default on purpose: a launchd job often runs when no
GUI session has unlocked the login Keychain, so Keychain reads can fail
silently. The `.env` file (git-ignored, `chmod 600`) just works. Keychain is
offered as a hardening upgrade for setups where the Mini auto-logs-in a user
whose Keychain is unlocked — see the README.
"""
from __future__ import annotations

import os
import subprocess

_KEYCHAIN_SERVICE = "sunday-brief"


def _from_keychain(name: str) -> str | None:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-a", name, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip()
    return None


def get_secret(name: str, *, required: bool = True, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val:
        return val
    if os.environ.get("SUNDAYBRIEF_USE_KEYCHAIN") == "1":
        val = _from_keychain(name)
        if val:
            return val
    if default is not None:
        return default
    if required:
        raise RuntimeError(
            f"Missing required secret '{name}'. Add it to your .env file "
            f"(or the Keychain if SUNDAYBRIEF_USE_KEYCHAIN=1)."
        )
    return None
