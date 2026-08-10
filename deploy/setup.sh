#!/usr/bin/env bash
# Oracle Cloud (Ubuntu 22.04) 인스턴스에서 1회 실행. train-bot과 같은 서버에 추가 배포.
# 사용법: DOMAIN=stocklens.1-2-3-4.nip.io ./setup.sh
set -euo pipefail

DOMAIN="${DOMAIN:?DOMAIN 환경변수를 지정하세요 (예: DOMAIN=stocklens.1-2-3-4.nip.io)}"
APP_DIR=/opt/stocklens

sudo apt-get install -y python3-venv python3-pip

id -u stocklens &>/dev/null || sudo useradd -r -m -s /usr/sbin/nologin stocklens

sudo mkdir -p "$APP_DIR"
sudo chown "$(whoami)":"$(whoami)" "$APP_DIR"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

sudo chown -R stocklens:stocklens "$APP_DIR"

sudo cp "$APP_DIR/deploy/stocklens.service" /etc/systemd/system/stocklens.service

# Caddyfile에 stocklens 사이트 블록이 없으면 추가 (기존 사이트는 건드리지 않음)
if ! sudo grep -q "$DOMAIN" /etc/caddy/Caddyfile 2>/dev/null; then
    printf '\n%s {\n    reverse_proxy 127.0.0.1:8767\n}\n' "$DOMAIN" | sudo tee -a /etc/caddy/Caddyfile >/dev/null
fi

sudo systemctl daemon-reload
sudo systemctl enable --now stocklens
sudo systemctl restart caddy

echo "완료: https://$DOMAIN 로 접속 가능 (방화벽 80/443 열려있어야 함)"
