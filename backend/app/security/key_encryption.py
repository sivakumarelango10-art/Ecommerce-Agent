from __future__ import annotations

import base64
import logging
from typing import Final

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretStr

logger = logging.getLogger(__name__)

CURRENT_CIPHERTEXT_VERSION: Final[str] = "v1"
SALT_USER_V1: Final[bytes] = b"storeflow-user-gemini-key-salt-v1"
INFO_USER_V1: Final[bytes] = b"user-gemini-key-fernet-encryption-v1"

# Backward compatibility aliases
SALT_V1: Final[bytes] = SALT_USER_V1
INFO_V1: Final[bytes] = INFO_USER_V1

# Dedicated platform company key encryption domain separation
SALT_PLATFORM_V1: Final[bytes] = b"storeflow-platform-ai-key-salt-v1"
INFO_PLATFORM_V1: Final[bytes] = b"platform-ai-key-fernet-encryption-v1"

MIN_KEY_SECRET_BYTES: Final[int] = 32


class KeyEncryptionError(RuntimeError):
    """Base exception for API key encryption and decryption failures."""


def derive_key_cipher(
    secret: SecretStr,
    version: str = CURRENT_CIPHERTEXT_VERSION,
    purpose: str = "user",
) -> Fernet:
    """Derive an authenticated Fernet cipher from the dedicated encryption secret using HKDF-SHA256.

    Ensures cryptographic domain separation between user personal keys and company platform keys.
    Never relies on authentication or other application secrets.
    """
    raw_secret = secret.get_secret_value()
    if not raw_secret or len(raw_secret.encode("utf-8")) < MIN_KEY_SECRET_BYTES:
        raise KeyEncryptionError(
            f"Encryption secret is insufficient: must be at least {MIN_KEY_SECRET_BYTES} bytes."
        )

    if version == "v1":
        if purpose == "user":
            salt = SALT_USER_V1
            info = INFO_USER_V1
        elif purpose == "platform":
            salt = SALT_PLATFORM_V1
            info = INFO_PLATFORM_V1
        else:
            raise KeyEncryptionError(f"Unsupported key purpose '{purpose}' for key derivation.")

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=info,
        )
    else:
        raise KeyEncryptionError(f"Unsupported cipher version '{version}' for key derivation.")

    derived_bytes = hkdf.derive(raw_secret.encode("utf-8"))
    fernet_key = base64.urlsafe_b64encode(derived_bytes)
    return Fernet(fernet_key)


def encrypt_user_key(
    plaintext_key: str,
    secret: SecretStr,
    version: str = CURRENT_CIPHERTEXT_VERSION,
) -> str:
    """Encrypt a plaintext Gemini API key into a versioned ciphertext token.

    Returns ciphertext formatted as '<version>:<fernet_token>'.
    Never logs or persists the plaintext key.
    """
    clean_key = plaintext_key.strip()
    if not clean_key:
        raise KeyEncryptionError("Cannot encrypt empty or blank API key.")

    cipher = derive_key_cipher(secret, version=version, purpose="user")
    encrypted_bytes = cipher.encrypt(clean_key.encode("utf-8"))
    return f"{version}:{encrypted_bytes.decode('utf-8')}"


def decrypt_user_key(
    versioned_ciphertext: str,
    secret: SecretStr,
) -> str:
    """Decrypt a versioned ciphertext token into the plaintext API key.

    In-memory decryption only; never log or persist the returned value.
    Raises KeyEncryptionError on tampering, corruption, wrong secret, or unsupported version.
    """
    clean_ct = versioned_ciphertext.strip()
    if not clean_ct:
        raise KeyEncryptionError("Ciphertext payload is empty.")

    parts = clean_ct.split(":", 1)
    if len(parts) != 2:
        raise KeyEncryptionError("Malformed versioned ciphertext: missing version prefix delimiter.")

    version, token_str = parts[0].strip(), parts[1].strip()
    if version != CURRENT_CIPHERTEXT_VERSION:
        raise KeyEncryptionError(f"Unsupported ciphertext version: '{version}'.")

    cipher = derive_key_cipher(secret, version=version, purpose="user")
    try:
        decrypted_bytes = cipher.decrypt(token_str.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except InvalidToken as exc:
        raise KeyEncryptionError(
            "Failed to decrypt user API key: ciphertext token is corrupted, tampered with, or used wrong secret."
        ) from exc
    except Exception as exc:
        raise KeyEncryptionError(
            "Unexpected error during user API key decryption."
        ) from exc


def mask_user_key(api_key: str) -> str:
    """Safely mask a Gemini API key displaying only the final four characters.

    Examples:
        'fake-test-key-material-cdef' -> '...cdef'
        '1234' -> '****'
        '' -> '...'
    """
    clean = api_key.strip()
    if not clean:
        return "..."
    if len(clean) <= 4:
        return "****"
    return f"...{clean[-4:]}"


def encrypt_platform_key(
    plaintext_key: str,
    secret: SecretStr,
    version: str = CURRENT_CIPHERTEXT_VERSION,
) -> str:
    """Encrypt a plaintext platform/company AI key into a versioned ciphertext token.

    Uses dedicated platform domain separation so platform keys cannot be decrypted
    with user-key contexts or vice-versa.
    Never logs or persists the plaintext key.
    """
    clean_key = plaintext_key.strip()
    if not clean_key:
        raise KeyEncryptionError("Cannot encrypt empty or blank API key.")

    cipher = derive_key_cipher(secret, version=version, purpose="platform")
    encrypted_bytes = cipher.encrypt(clean_key.encode("utf-8"))
    return f"{version}:{encrypted_bytes.decode('utf-8')}"


def decrypt_platform_key(
    versioned_ciphertext: str,
    secret: SecretStr,
) -> str:
    """Decrypt a versioned ciphertext token into the plaintext platform API key.

    In-memory decryption only; never log or persist the returned value.
    Raises KeyEncryptionError on tampering, corruption, wrong secret, or unsupported version.
    """
    clean_ct = versioned_ciphertext.strip()
    if not clean_ct:
        raise KeyEncryptionError("Ciphertext payload is empty.")

    parts = clean_ct.split(":", 1)
    if len(parts) != 2:
        raise KeyEncryptionError("Malformed versioned ciphertext: missing version prefix delimiter.")

    version, token_str = parts[0].strip(), parts[1].strip()
    if version != CURRENT_CIPHERTEXT_VERSION:
        raise KeyEncryptionError(f"Unsupported ciphertext version: '{version}'.")

    cipher = derive_key_cipher(secret, version=version, purpose="platform")
    try:
        decrypted_bytes = cipher.decrypt(token_str.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except InvalidToken as exc:
        raise KeyEncryptionError(
            "Failed to decrypt platform API key: ciphertext token is corrupted, tampered with, or used wrong secret."
        ) from exc
    except Exception as exc:
        raise KeyEncryptionError(
            "Unexpected error during platform API key decryption."
        ) from exc


def mask_platform_key(api_key: str) -> str:
    """Safely mask a platform API key displaying only the final four characters."""
    return mask_user_key(api_key)
