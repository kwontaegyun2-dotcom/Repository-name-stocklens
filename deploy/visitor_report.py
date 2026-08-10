#!/usr/bin/env python3
"""stocklens 방문자 통계 - Caddy 액세스 로그 파싱 + IP 지역조회 (표준 라이브러리만 사용)
사용법: python3 visitor_report.py [최근일수(기본 1)]
"""
import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

LOG_PATH = "/var/log/caddy/stocklens-access.log"
CACHE_PATH = "/opt/stocklens/geo_cache.json"
KST = ZoneInfo("Asia/Seoul")


def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def geolocate(ips, cache):
    todo = [ip for ip in ips if ip not in cache]
    for i in range(0, len(todo), 100):
        batch = todo[i:i + 100]
        req = urllib.request.Request(
            "http://ip-api.com/batch?fields=status,country,regionName,city,query",
            data=json.dumps(batch).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                results = json.loads(resp.read())
            for r in results:
                q = r.get("query", "?")
                if r.get("status") == "success":
                    cache[q] = f"{r.get('country', '?')} {r.get('regionName', '')} {r.get('city', '')}".strip()
                else:
                    cache[q] = "조회실패"
        except Exception as e:
            for ip in batch:
                cache.setdefault(ip, f"조회실패({e})")
    return cache


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    if not os.path.exists(LOG_PATH):
        print("아직 접속 로그 파일이 없습니다 (로깅 시작 이후 방문이 없었거나 방금 켰습니다).")
        return

    by_day = defaultdict(list)
    with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            req = rec.get("request", {})
            ip = req.get("client_ip") or req.get("remote_ip")
            ts = rec.get("ts")
            if not ip or not ts:
                continue
            dt = datetime.fromtimestamp(ts, KST)
            day = dt.strftime("%Y-%m-%d")
            by_day[day].append({"ip": ip, "time": dt.strftime("%H:%M:%S"), "path": req.get("uri", "")})

    if not by_day:
        print("파싱된 로그 라인이 없습니다.")
        return

    all_days = sorted(by_day.keys(), reverse=True)[:days]
    cache = load_cache()
    all_ips = {r["ip"] for d in all_days for r in by_day[d]}
    cache = geolocate(all_ips, cache)
    save_cache(cache)

    for day in all_days:
        rows = by_day[day]
        ip_counts = Counter(r["ip"] for r in rows)
        print(f"\n=== {day} ===  총 요청 {len(rows)}건 / 고유 방문 IP {len(ip_counts)}개")
        for ip, cnt in ip_counts.most_common():
            print(f"  {ip:15s}  {cnt:4d}회   {cache.get(ip, '?')}")


if __name__ == "__main__":
    main()
