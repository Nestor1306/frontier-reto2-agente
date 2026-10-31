#!/bin/zsh
# RESPALDO: publica el contenedor local con un Cloudflare Quick Tunnel (sin cuenta).
# La Mac debe quedar encendida, sin reposo y con internet mientras se use la URL.
# Si el túnel se reinicia, la URL https://*.trycloudflare.com CAMBIA.
# Requisitos: Docker Desktop abierto, `brew install cloudflared` y un archivo .env en esta carpeta.
set -e
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Falta .env (copia .env.example y llénalo)"; exit 1; }
docker build -t frontier-agente:local .
docker rm -f frontier-tunel >/dev/null 2>&1 || true
docker run -d --name frontier-tunel --restart unless-stopped -p 8000:8000 --env-file .env frontier-agente:local
sleep 3
curl -fsS http://127.0.0.1:8000/health && echo
echo "Abriendo túnel… la URL pública aparece abajo (https://….trycloudflare.com)"
exec cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000
