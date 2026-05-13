#!/usr/bin/env bash
# Pinhaoke 一键更新脚本
# 用法（服务器上）: sudo bash /opt/pinhaoke/deploy/update.sh
#
# 做的事：拉最新代码 → 必要时重建 venv → 刷新 systemd unit → 重启服务 → 烟测

set -euo pipefail

APP_DIR=/opt/pinhaoke
APP_USER=www-data
SERVICE=pinhaoke

cd "$APP_DIR"

echo "==> [1/6] 停服务"
systemctl stop "$SERVICE" || true

echo "==> [2/6] 拉取最新代码（强制对齐 origin/main）"
git fetch origin
git reset --hard origin/main

echo "==> [3/6] 检查依赖"
NEED_REBUILD=0
if [ ! -x venv/bin/python ]; then
    NEED_REBUILD=1
elif [ requirements.txt -nt venv/.installed ]; then
    NEED_REBUILD=1
fi

if [ "$NEED_REBUILD" = "1" ]; then
    echo "    requirements 有变化或 venv 不存在，重建中..."
    rm -rf venv
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip --quiet
    ./venv/bin/pip install -r requirements.txt --quiet
    touch venv/.installed
else
    echo "    requirements 未变，跳过"
fi

echo "==> [4/6] 修正权限"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> [5/6] 刷新 systemd unit"
cp "$APP_DIR/deploy/pinhaoke.service" /etc/systemd/system/"$SERVICE".service
systemctl daemon-reload

echo "==> [6/6] 启动服务并烟测"
systemctl start "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" && echo "    服务已 active" || {
    echo "    ❌ 服务启动失败，查日志："
    journalctl -u "$SERVICE" -n 30 --no-pager
    exit 1
}

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "    HTTP 200 ✅"
else
    echo "    ⚠️  本地探活返回 $HTTP_CODE，请检查"
fi

echo "✅ 更新完成"
