#!/usr/bin/env bash
# QuantRank500 PROD deploy — dark until launch. Run from the bundle directory:
#     cd ~/prod-bundle && bash deploy.sh
# Prod is born pristine: no dumps. The worker creates all schemas at startup;
# the ledger's first record will be a real one.
set -euo pipefail

echo "== 1/4 Docker =="
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    echo "Docker installed. Log out/in (or run: newgrp docker) then re-run deploy.sh"
    exit 0
fi

echo "== 2/4 .env =="
if [ ! -f .env ]; then
    cp env.example .env
    sed -i "s/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
    sed -i "s/^UMAMI_APP_SECRET=$/UMAMI_APP_SECRET=$(openssl rand -hex 32)/" .env
    echo "Created .env with random POSTGRES_PASSWORD and UMAMI_APP_SECRET."
fi
# TUNNEL_TOKEN is intentionally NOT required — dark prod runs without ingress.

echo "== 3/4 Database =="
docker compose up -d db
until docker compose exec -T db pg_isready -U quantrank -d quantrank500_prod >/dev/null 2>&1; do
    sleep 2
done
if ! docker compose exec -T db psql -U quantrank -d quantrank500_prod -tAc \
    "SELECT 1 FROM pg_database WHERE datname = 'umami'" | grep -q 1; then
    docker compose exec -T db psql -q -U quantrank -d quantrank500_prod \
        -c "CREATE DATABASE umami"
    echo "Created umami database."
fi

echo "== 4/4 Build + start (DARK: no cloudflared) =="
docker compose up -d --build

docker compose ps
echo ""
echo "Dark prod is up. Launch day: put TUNNEL_TOKEN in .env, then"
echo "    docker compose --profile public up -d"
