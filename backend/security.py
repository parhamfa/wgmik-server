from cryptography.fernet import Fernet, InvalidToken
from hashlib import sha256
from typing import Optional


def _derive_key_from_secret(secret: str) -> bytes:
    # Use SHA-256 to derive a 32-byte key, then base64 encode for Fernet
    import base64
    digest = sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class SecretBox:
    def __init__(self, secret: str):
        self._fernet = Fernet(_derive_key_from_secret(secret))

    def encrypt(self, plaintext: str) -> str:
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, token: str) -> Optional[str]:
        try:
            data = self._fernet.decrypt(token.encode("utf-8"))
            return data.decode("utf-8")
        except InvalidToken:
            return None


