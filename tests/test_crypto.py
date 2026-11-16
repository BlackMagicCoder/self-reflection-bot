import pytest
from cryptography.fernet import Fernet, InvalidToken

import crypto

USER = 42
OTHER_USER = 43


@pytest.fixture(autouse=True)
def reset_key():
    crypto.clear_key(USER)
    crypto.clear_key(OTHER_USER)
    yield
    crypto.clear_key(USER)
    crypto.clear_key(OTHER_USER)


def test_locked_by_default():
    assert crypto.is_unlocked(USER) is False


def test_set_key_unlocks():
    crypto.set_key(USER, Fernet.generate_key().decode())
    assert crypto.is_unlocked(USER) is True


def test_set_key_rejects_invalid_key():
    with pytest.raises(Exception):
        crypto.set_key(USER, "not-a-valid-fernet-key")
    assert crypto.is_unlocked(USER) is False


def test_clear_key_relocks():
    crypto.set_key(USER, Fernet.generate_key().decode())
    crypto.clear_key(USER)
    assert crypto.is_unlocked(USER) is False


def test_get_fernet_raises_when_locked():
    with pytest.raises(RuntimeError):
        crypto.get_fernet(USER)


def test_encrypt_decrypt_roundtrip():
    crypto.set_key(USER, Fernet.generate_key().decode())
    token = crypto.encrypt_data(USER, "geheimer text äöü")
    assert crypto.decrypt_data(USER, token) == "geheimer text äöü"


def test_decrypt_raises_when_locked():
    crypto.set_key(USER, Fernet.generate_key().decode())
    token = crypto.encrypt_data(USER, "hallo")
    crypto.clear_key(USER)
    with pytest.raises(RuntimeError):
        crypto.decrypt_data(USER, token)


def test_decrypt_with_wrong_key_raises_invalid_token():
    crypto.set_key(USER, Fernet.generate_key().decode())
    token = crypto.encrypt_data(USER, "hallo")
    crypto.set_key(USER, Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        crypto.decrypt_data(USER, token)


def test_encrypt_decrypt_file_roundtrip(tmp_path):
    crypto.set_key(USER, Fernet.generate_key().decode())
    path = tmp_path / "note.md"
    path.write_text("Hintergrundinfos über mich", encoding="utf-8")
    crypto.encrypt_file(USER, path)
    assert path.read_bytes() != "Hintergrundinfos über mich".encode("utf-8")
    crypto.decrypt_file(USER, path)
    assert path.read_text(encoding="utf-8") == "Hintergrundinfos über mich"


def test_users_have_independent_keys():
    crypto.set_key(USER, Fernet.generate_key().decode())
    token = crypto.encrypt_data(USER, "geheim")
    assert crypto.is_unlocked(OTHER_USER) is False
    with pytest.raises(RuntimeError):
        crypto.decrypt_data(OTHER_USER, token)
    crypto.set_key(OTHER_USER, Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        crypto.decrypt_data(OTHER_USER, token)
