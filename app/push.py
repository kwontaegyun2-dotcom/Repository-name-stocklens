# -*- coding: utf-8 -*-
"""웹푸시(VAPID) — 브라우저가 꺼져 있어도(백그라운드) 알림이 뜨게 한다.

VAPID 키쌍은 data_dir에 한 번 만들어 계속 재사용한다. 키가 바뀌면 기존 구독이
전부 무효가 되므로 절대 지우지 말 것 (ddok-alarm 프로젝트와 동일 패턴).
"""
import base64
import json
import sqlite3
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

VAPID_SUBJECT = "mailto:kwontaegyun2@gmail.com"

_private_pem = None
_public_txt = None
PUBLIC_KEY = None


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def init(data_dir: Path):
    global _private_pem, _public_txt, PUBLIC_KEY
    _private_pem = data_dir / "vapid_private.pem"
    _public_txt = data_dir / "vapid_public.txt"

    if _private_pem.exists() and _public_txt.exists():
        PUBLIC_KEY = _public_txt.read_text(encoding="utf-8").strip()
        return

    private = ec.generate_private_key(ec.SECP256R1())
    _private_pem.write_bytes(private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    PUBLIC_KEY = _b64url(public_raw)
    _public_txt.write_text(PUBLIC_KEY, encoding="utf-8")


def send_one(sub_row: sqlite3.Row, payload: dict) -> tuple[bool, str]:
    """구독 1건에 발송. 만료된 구독(404/410)이면 (False, "expired")를 돌려준다."""
    subscription = {
        "endpoint": sub_row["endpoint"],
        "keys": {"p256dh": sub_row["p256dh"], "auth": sub_row["auth"]},
    }
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=str(_private_pem),
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=300,
            timeout=10,
        )
        return True, "ok"
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            return False, "expired"
        return False, f"푸시 실패({status})"
    except Exception as exc:
        return False, f"푸시 오류: {exc}"[:200]
