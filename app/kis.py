# -*- coding: utf-8 -*-
"""한국투자증권 오픈API 연동 (실시간 현재가). 앱키 미설정 시 네이버 데이터로 폴백."""
import json
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "kis_config.json"
TOKEN_FILE = BASE_DIR / "kis_token.json"

REAL_URL = "https://openapi.koreainvestment.com:9443"
PAPER_URL = "https://openapivts.koreainvestment.com:29443"


def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def save_config(app_key: str, app_secret: str, is_paper: bool = False):
    CONFIG_FILE.write_text(
        json.dumps({"app_key": app_key, "app_secret": app_secret, "is_paper": is_paper},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def is_configured() -> bool:
    cfg = load_config()
    return bool(cfg and cfg.get("app_key") and cfg.get("app_secret"))


def _base_url(cfg) -> str:
    return PAPER_URL if cfg.get("is_paper") else REAL_URL


def _get_token(cfg) -> str:
    if TOKEN_FILE.exists():
        try:
            tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            if tok.get("expires_at", 0) > time.time() + 60:
                return tok["access_token"]
        except Exception:
            pass
    r = requests.post(
        f"{_base_url(cfg)}/oauth2/tokenP",
        json={"grant_type": "client_credentials",
              "appkey": cfg["app_key"], "appsecret": cfg["app_secret"]},
        timeout=10)
    r.raise_for_status()
    data = r.json()
    token = data["access_token"]
    TOKEN_FILE.write_text(
        json.dumps({"access_token": token,
                    "expires_at": time.time() + int(data.get("expires_in", 86400))}),
        encoding="utf-8")
    return token


def current_price(code: str):
    """주식현재가 시세 조회. 실패 시 예외 발생 → 호출측에서 네이버 폴백."""
    cfg = load_config()
    if not cfg:
        raise RuntimeError("KIS not configured")
    token = _get_token(cfg)
    r = requests.get(
        f"{_base_url(cfg)}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers={
            "authorization": f"Bearer {token}",
            "appkey": cfg["app_key"],
            "appsecret": cfg["app_secret"],
            "tr_id": "FHKST01010100",
        },
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1", "KIS error"))
    o = data["output"]
    return {
        "source": "KIS",
        "price": float(o["stck_prpr"]),
        "change": float(o["prdy_vrss"]),
        "rate": float(o["prdy_ctrt"]),
        "open": float(o["stck_oprc"]),
        "high": float(o["stck_hgpr"]),
        "low": float(o["stck_lwpr"]),
        "volume": float(o["acml_vol"]),
        "value": float(o["acml_tr_pbmn"]),
        "per": o.get("per"),
        "pbr": o.get("pbr"),
        "market_cap": o.get("hts_avls"),
    }
