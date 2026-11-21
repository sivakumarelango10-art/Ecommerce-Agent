import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretStr


def get_outbox_cipher(secret: SecretStr) -> Fernet:
    """Derive a dedicated Fernet authenticated cipher from the OTP secret using HKDF."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"ecommerce-guest-email-outbox-salt",
        info=b"otp-outbox-fernet-encryption",
    )
    derived_key = base64.urlsafe_b64encode(
        hkdf.derive(secret.get_secret_value().encode("utf-8"))
    )
    return Fernet(derived_key)


def encrypt_otp(otp: str, secret: SecretStr) -> str:
    """Encrypt a plaintext OTP for zero-plaintext outbox persistence."""
    cipher = get_outbox_cipher(secret)
    return cipher.encrypt(otp.encode("utf-8")).decode("utf-8")


def decrypt_otp(encrypted_payload: str, secret: SecretStr) -> str:
    """Decrypt an encrypted outbox OTP token in memory only at delivery time."""
    cipher = get_outbox_cipher(secret)
    return cipher.decrypt(encrypted_payload.encode("utf-8")).decode("utf-8")
