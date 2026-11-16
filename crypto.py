"""Fernet encryption with RAM-only, per-user keys (key-on-demand via /unlock).

Each user (keyed by their Telegram chat_id) has their own Fernet key, held only
in a module-level dict — never written to disk, logs, or env files. After every
process start every user is locked until they send /unlock <key> themselves;
`set_key` keeps their key in RAM only. One user unlocking never affects another
user's lock state or uses another user's key. No Telegram or storage-layout
knowledge lives here — pure primitives.
"""
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken  # noqa: F401  (re-exported)

_fernets: dict[int, Fernet] = {}


def is_unlocked(user_id: int) -> bool:
    return user_id in _fernets


def set_key(user_id: int, key: str) -> None:
    """Validate the key with an encrypt/decrypt round trip, then keep it in RAM for this user."""
    f = Fernet(key.strip().encode())
    if f.decrypt(f.encrypt(b"probe")) != b"probe":
        raise ValueError("Round-trip test failed")
    _fernets[user_id] = f


def clear_key(user_id: int) -> None:
    _fernets.pop(user_id, None)


def get_fernet(user_id: int) -> Fernet:
    f = _fernets.get(user_id)
    if f is None:
        raise RuntimeError(f"User {user_id} is locked — no Fernet key set. Use /unlock first.")
    return f


def encrypt_data(user_id: int, data: str) -> bytes:
    return get_fernet(user_id).encrypt(data.encode("utf-8"))


def decrypt_data(user_id: int, data: bytes) -> str:
    return get_fernet(user_id).decrypt(data).decode("utf-8")


def encrypt_file(user_id: int, path: Path) -> None:
    path.write_bytes(get_fernet(user_id).encrypt(path.read_bytes()))


def decrypt_file(user_id: int, path: Path) -> None:
    path.write_bytes(get_fernet(user_id).decrypt(path.read_bytes()))
