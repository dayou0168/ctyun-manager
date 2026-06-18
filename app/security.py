import base64
import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


_key_lock = threading.Lock()


def _fallback_key() -> bytes:
    digest = hashlib.sha256(settings.session_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _configured_key() -> bytes | None:
    if not settings.master_key:
        return None
    try:
        key = settings.master_key.strip().encode("utf-8")
        Fernet(key)
        return key
    except (TypeError, ValueError):
        return None


def _managed_key_path() -> Path:
    return Path(settings.db_path).resolve().parent / "master.key"


def _managed_key() -> bytes:
    path = _managed_key_path()
    with _key_lock:
        if path.exists():
            key = path.read_text(encoding="ascii").strip().encode("ascii")
            try:
                Fernet(key)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"持久化加密密钥文件无效：{path}") from exc
            return key

        path.parent.mkdir(parents=True, exist_ok=True)
        key = _configured_key() or Fernet.generate_key()
        path.write_text(key.decode("ascii"), encoding="ascii")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key


def encryption_key_status() -> str:
    _managed_key()
    return "managed"


def _candidate_keys() -> list[bytes]:
    candidates = [_managed_key()]
    configured = _configured_key()
    if configured:
        candidates.append(configured)
    candidates.append(_fallback_key())
    return list(dict.fromkeys(candidates))


def fernet() -> Fernet:
    return Fernet(_managed_key())


def encrypt_text(value: str | None) -> str | None:
    if not value:
        return None
    return fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(value: str | None) -> str | None:
    if not value:
        return None
    encoded = value.encode("utf-8")
    for key in _candidate_keys():
        try:
            return Fernet(key).decrypt(encoded).decode("utf-8")
        except InvalidToken:
            continue
    return None


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 210_000)
    return base64.b64encode(salt + digest).decode("ascii")


def verify_password(password: str, encoded: str) -> bool:
    raw = base64.b64decode(encoded.encode("ascii"))
    salt, expected = raw[:16], raw[16:]
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 210_000)
    return hmac.compare_digest(actual, expected)


def sign_session(payload: dict[str, Any]) -> str:
    body = dict(payload)
    body["iat"] = int(time.time())
    data = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8")).decode("ascii")
    sig = hmac.new(settings.session_secret.encode("utf-8"), data.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{data}.{sig}"


def verify_session(token: str | None, max_age_seconds: int = 86400) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    data, sig = token.rsplit(".", 1)
    expected = hmac.new(settings.session_secret.encode("utf-8"), data.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    payload = json.loads(base64.urlsafe_b64decode(data.encode("ascii")))
    if int(time.time()) - int(payload.get("iat", 0)) > max_age_seconds:
        return None
    return payload


def mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return f"{value[:2]}***{value[-2:]}"
