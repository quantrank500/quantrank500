#!/usr/bin/env bash
# QuantRank500 demo VM deploy. Run from the bundle directory on the VM:
#     cd ~/demo-bundle && bash deploy.sh
set -euo pipefail

echo "== 1/5 Docker =="
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    echo "Docker installed. Log out/in (or run: newgrp docker) then re-run deploy.sh"
    exit 0
fi

echo "== 2/5 .env =="
if [ ! -f .env ]; then
    cp env.example .env
    # random db password, local to this VM only
    sed -i "s/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
    echo "Created .env with a random POSTGRES_PASSWORD."
fi
if ! grep -q "^TUNNEL_TOKEN=.\+" .env; then
    echo "TUNNEL_TOKEN is empty in .env — paste the Cloudflare tunnel token, then re-run."
    exit 1
fi
if ! grep -q "^UMAMI_APP_SECRET=.\+" .env; then
    if grep -q "^UMAMI_APP_SECRET=" .env; then
        sed -i "s/^UMAMI_APP_SECRET=$/UMAMI_APP_SECRET=$(openssl rand -hex 32)/" .env
    else
        echo "UMAMI_APP_SECRET=$(openssl rand -hex 32)" >> .env
    fi
    echo "Generated UMAMI_APP_SECRET."
fi

echo "== 3/5 Database up + load dumps =="
docker compose up -d db
until docker compose exec -T db pg_isready -U quantrank -d quantrank500_demo >/dev/null 2>&1; do
    sleep 2
done
LOADED=$(docker compose exec -T db psql -U quantrank -d quantrank500_demo -tAc \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='predictions'")
if [ "$LOADED" = "0" ]; then
    docker compose exec -T db psql -q -U quantrank -d quantrank500_demo -f /dumps/demo_app.sql
    docker compose exec -T db psql -q -U quantrank -d quantrank500_demo \
        -c "CREATE SCHEMA IF NOT EXISTS lake"
    docker compose exec -T db psql -q -U quantrank -d quantrank500_demo -f /dumps/lake_daily.sql
    echo "Dumps loaded."
else
    echo "Database already loaded; skipping dumps."
fi

if ! docker compose exec -T db psql -U quantrank -d quantrank500_demo -tAc \
    "SELECT 1 FROM pg_database WHERE datname = 'umami'" | grep -q 1; then
    docker compose exec -T db psql -q -U quantrank -d quantrank500_demo \
        -c "CREATE DATABASE umami"
    echo "Created umami database."
fi

echo "== 4/5 Build + start everything =="
docker compose up -d --build

echo "== 5/5 Status =="
docker compose ps
echo ""
echo "If the tunnel is configured for demo.quantrank500.com, the site is live there."
