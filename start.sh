#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

mkdir -p data secrets/de secrets/en

if [ ! -f .env ]; then
  cp .env.example .env
  printf '%s\n' ".env wurde aus .env.example erstellt."
  printf '%s\n' "Trage jetzt erst deine Zugangsdaten ein und starte dann erneut."
  exit 1
fi

bash ./doctor.sh

docker compose up -d --build

printf '%s\n' ""
printf '%s\n' "ClipFactory startet."
printf '%s\n' "Logs: docker compose logs -f"
printf '%s\n' "Health: curl http://127.0.0.1:8080/health"
