# -*- coding: utf-8 -*-
"""DART 오픈API — 축2(실적 변곡) 데이터.

분기 영업이익의 전년동기 대비 증감(OP_YoY)과 적자→흑자 전환 플래그를 제공한다.
키(DART_API_KEY)가 없으면 조용히 비활성 — 스크리너는 기존 공식으로 폴백한다.

호출량: 다중회사 주요계정(fnlttMultiAcnt)은 corp_code를 100개까지 묶어 1콜.
400종목이면 4콜이면 끝난다. (DART 한도 20,000콜/일)
"""
import io
import os
import threading
import time
import xml.etree.ElementTree as ET
import zipfile

import requests

BASE = "https://opendart.fss.or.kr/api"
BATCH = 100          # fnlttMultiAcnt 의 corp_code 최대 개수
REFRESH_SEC = 21600  # 6시간 (스크리너와 동일 주기)

# 보고서 코드: 1분기 / 반기 / 3분기 / 사업보고서
Q_CODES = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}

_lock = threading.Lock()
_corp_map: dict = {}        # 종목코드(6자리) -> corp_code(8자리)
_corp_at = 0.0
_cache: dict = {}           # 종목코드 -> {op_yoy, turnaround, ...}
_cache_at = 0.0
_period = ""                # 화면 표기용 기준 분기 (예: "2026 1분기")


def _key() -> str:
    """환경변수 우선, 없으면 로컬 파일(dart_key.txt). 둘 다 커밋 대상 아님."""
    k = (os.environ.get("DART_API_KEY") or "").strip()
    if k:
        return k
    try:
        p = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dart_key.txt")
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def available() -> bool:
    return bool(_key())


def _amt(v):
    """DART 금액 문자열 → float(원). '-', '', None 은 None."""
    if v is None:
        return None
    s = str(v).replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "–", "—"):
        return None
    s = s.replace("△", "-")  # 일부 항목이 음수를 △로 주는 경우
    try:
        return float(s)
    except ValueError:
        return None


def corp_map() -> dict:
    """종목코드 → corp_code 매핑. corpCode.xml(zip)을 받아 파싱하고 하루 캐시."""
    global _corp_map, _corp_at
    with _lock:
        if _corp_map and time.time() - _corp_at < 86400:
            return dict(_corp_map)
    key = _key()
    if not key:
        return {}
    try:
        r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=30)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml = z.read(z.namelist()[0])
        out = {}
        for el in ET.fromstring(xml).iter("list"):
            sc = (el.findtext("stock_code") or "").strip()
            cc = (el.findtext("corp_code") or "").strip()
            if sc and cc and sc.isdigit() and len(sc) == 6:
                out[sc] = cc
        with _lock:
            _corp_map, _corp_at = out, time.time()
        return dict(out)
    except Exception:
        return {}


def _report_candidates():
    """최근 공시부터 역순으로 시도할 (사업연도, 보고서코드) 목록.

    분기보고서는 분기 종료 후 45일 내 제출이라, 여유를 두고 현재 시점 기준
    최근 것부터 훑는다. 첫 응답이 오는 조합을 채택.
    """
    t = time.localtime()
    y, m = t.tm_year, t.tm_mon
    cands = []
    if m >= 11:
        cands.append((y, "11014"))    # 3분기
    if m >= 8:
        cands.append((y, "11012"))    # 반기
    if m >= 5:
        cands.append((y, "11013"))    # 1분기
    # 직전 연도로 폴백
    cands += [(y - 1, "11011"), (y - 1, "11014"), (y - 1, "11012"), (y - 1, "11013")]
    return cands


def _fetch_batch(corp_codes, year, reprt):
    key = _key()
    r = requests.get(f"{BASE}/fnlttMultiAcnt.json", timeout=30, params={
        "crtfc_key": key,
        "corp_code": ",".join(corp_codes),
        "bsns_year": str(year),
        "reprt_code": reprt,
    })
    r.raise_for_status()
    d = r.json()
    if d.get("status") != "000":
        return []
    return d.get("list") or []


def _parse(items):
    """주요계정 응답 → {종목코드: {op, op_prev, op_yoy, turnaround}}.

    영업이익만 사용. 연결(CFS) 우선, 없으면 별도(OFS).
    분기 단독 금액(thstrm_amount/frmtrm_amount)을 우선 쓰고,
    비면 누적(add_amount) 쌍으로 폴백한다.
    """
    best = {}
    for it in items:
        if "영업이익" not in (it.get("account_nm") or ""):
            continue
        code = (it.get("stock_code") or "").strip()
        if not code or not code.isdigit():
            continue
        cur = _amt(it.get("thstrm_amount"))
        prev = _amt(it.get("frmtrm_amount"))
        if cur is None or prev is None:
            cur = _amt(it.get("thstrm_add_amount"))
            prev = _amt(it.get("frmtrm_add_amount"))
        if cur is None or prev is None:
            continue
        fs = (it.get("fs_div") or "").upper()
        rank = 0 if fs == "CFS" else 1          # 연결 우선
        if code in best and best[code][0] <= rank:
            continue
        best[code] = (rank, cur, prev)

    out = {}
    for code, (_, cur, prev) in best.items():
        if prev == 0:
            continue
        # 전년이 적자여도 분모를 절대값으로 두면 부호가 뒤집히지 않는다.
        # (적자축소·흑자전환은 +, 적자확대는 -)
        yoy = (cur - prev) / abs(prev) * 100
        out[code] = {
            "op": cur,
            "op_prev": prev,
            "op_yoy": round(max(-300.0, min(300.0, yoy)), 1),   # 이상치 클리핑
            "turnaround": bool(prev <= 0 < cur),
        }
    return out


def get(codes) -> dict:
    """종목코드 목록 → 실적 변곡 지표. 키 없거나 실패 시 빈 dict(=폴백)."""
    global _cache, _cache_at, _period
    if not available():
        return {}
    with _lock:
        if _cache and time.time() - _cache_at < REFRESH_SEC:
            return {c: _cache[c] for c in codes if c in _cache}

    cmap = corp_map()
    if not cmap:
        return {}
    targets = [(c, cmap[c]) for c in codes if c in cmap]
    if not targets:
        return {}

    for year, reprt in _report_candidates():
        merged = {}
        for i in range(0, len(targets), BATCH):
            chunk = [cc for _, cc in targets[i:i + BATCH]]
            try:
                merged.update(_parse(_fetch_batch(chunk, year, reprt)))
            except Exception:
                continue
        # 표본이 너무 적으면 해당 분기는 아직 공시 전 → 다음 후보로
        if len(merged) >= max(10, len(targets) // 10):
            with _lock:
                _cache, _cache_at = merged, time.time()
                _period = f"{year} {Q_CODES.get(reprt, '')}분기"
            return {c: merged[c] for c in codes if c in merged}
    return {}


def period() -> str:
    with _lock:
        return _period
