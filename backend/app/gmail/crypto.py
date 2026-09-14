from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet() -> Fernet:
    # Built per call (not cached at import time) so a bad/missing key
    # surfaces only when encryption is actually used, not at import time —
    # matches the lazy, settings-read-per-call style of app/auth/jwt.py.
    return Fernet(settings.gmail_token_encryption_key.encode())


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
