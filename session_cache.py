"""Persist browser state as authenticated ciphertext, never as a cache secret."""

import hashlib
import json
import logging
import os
from pathlib import Path

from cas_auth import AuthenticationError, load_browser_state, school_state


MAGIC = b"XJTU-SESSION-1:"


def cache_scope(seed):
    return hashlib.sha256(("xjtu-session-scope:" + seed).encode("utf-8")).hexdigest()[:24]


def cache_key(seed):
    # The seed is a high-entropy school browser session held in Actions Secrets.
    # Rotating that Secret also rotates the encryption key and cache namespace.
    return hashlib.sha256(("xjtu-session-encryption-v1:" + seed).encode("utf-8")).digest()


def restore_state(seed, cache_path=None):
    initial = load_browser_state(seed)
    if not initial or not cache_path or not Path(cache_path).is_file():
        return initial
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        path = Path(cache_path)
        if path.stat().st_size > 200000:
            raise ValueError
        data = path.read_bytes()
        if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 28:
            raise ValueError
        nonce, ciphertext = data[len(MAGIC):len(MAGIC) + 12], data[len(MAGIC) + 12:]
        raw = AESGCM(cache_key(seed)).decrypt(nonce, ciphertext, MAGIC)
        state = load_browser_state(raw.decode("utf-8"))
        if not state:
            raise ValueError
        logging.info("已恢复上次成功登录后加密保存的学校会话。")
        return state
    except (OSError, ValueError, UnicodeError, InvalidTag, AuthenticationError):
        logging.warning("会话缓存无法校验，将使用 Actions Secret 中的初始会话。")
        return initial


def persist_state(seed, cache_path, state):
    if not seed or not cache_path:
        return
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        raw = json.dumps(school_state(state), ensure_ascii=True, separators=(",", ":"))
        load_browser_state(raw)
        if len(raw.encode("utf-8")) > 100000:
            raise ValueError
        nonce = os.urandom(12)
        data = MAGIC + nonce + AESGCM(cache_key(seed)).encrypt(nonce, raw.encode("utf-8"), MAGIC)
        path = Path(cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
        path.with_suffix(".updated").touch()
        logging.info("本次成功登录后的学校会话已加密保存。")
    except (OSError, ValueError, AuthenticationError):
        logging.warning("本次登录成功，但无法保存加密会话缓存。")
