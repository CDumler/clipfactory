#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

status=0

say() {
  printf '%s\n' "$*"
}

fail() {
  say "FEHLT: $*"
  status=1
}

warn() {
  say "HINWEIS: $*"
}

require_var() {
  local name="$1"
  local value="${!name:-}"
  if [ -z "$value" ]; then
    fail "$name ist leer"
  fi
}

check_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    fail "$path fehlt"
  fi
}

profile_tag() {
  case "$1" in
    de) printf '%s' "DE" ;;
    en) printf '%s' "EN" ;;
    *) printf '%s' "$1" | tr '[:lower:]' '[:upper:]' ;;
  esac
}

check_profile() {
  local profile="$1"
  local enabled="$2"
  local tag
  tag="$(profile_tag "$profile")"

  case ",$enabled," in
    *,youtube,*)
      check_file "secrets/$profile/token.json"
      ;;
  esac

  case ",$enabled," in
    *,instagram,*)
      require_var "IG_USER_ID_${tag}"
      require_var "IG_ACCESS_TOKEN_${tag}"
      if [ -z "${PUBLIC_BASE_URL:-}" ] && [ -z "${PUBLIC_HOSTNAME:-}" ]; then
        fail "PUBLIC_BASE_URL oder PUBLIC_HOSTNAME wird fuer Instagram gebraucht"
      fi
      ;;
  esac

  case ",$enabled," in
    *,tiktok,*)
      require_var "TIKTOK_ACCESS_TOKEN_${tag}"
      ;;
  esac
}

say "ClipFactory Doctor"

if ! command -v docker >/dev/null 2>&1; then
  fail "docker ist nicht installiert. Auf dem Mac: Docker Desktop installieren."
else
  say "OK: docker gefunden"
fi

if command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    say "OK: docker compose verfuegbar"
  else
    fail "docker compose ist nicht verfuegbar"
  fi
fi

if [ ! -f .env ]; then
  fail ".env fehlt (erst: cp .env.example .env)"
else
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a

  require_var TWITCH_CLIENT_ID
  require_var TWITCH_CLIENT_SECRET
  require_var TELEGRAM_BOT_TOKEN
  if [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
    warn "TELEGRAM_CHAT_ID ist noch leer. Du kannst den Stack trotzdem starten und dem Bot spaeter /id schreiben."
  fi
  require_var OPENAI_API_KEY

  PLATFORMS_DE="${PLATFORMS_DE:-${PLATFORMS:-youtube}}"
  PLATFORMS_EN="${PLATFORMS_EN:-${PLATFORMS:-youtube}}"

  check_profile de "$PLATFORMS_DE"
  check_profile en "$PLATFORMS_EN"

  if [ "${YOUTUBE_PRIVACY_STATUS_DE:-private}" = "public" ] || [ "${YOUTUBE_PRIVACY_STATUS_EN:-private}" = "public" ]; then
    warn "Neue, nicht auditierte YouTube-Projekte laden laut Google standardmaessig nur privat hoch."
  fi
fi

if [ -f discovery.json ]; then
  say "OK: discovery.json gefunden"
else
  fail "discovery.json fehlt"
fi

if command -v docker >/dev/null 2>&1 && docker compose ps >/dev/null 2>&1; then
  if command -v curl >/dev/null 2>&1 && curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
    say "OK: API antwortet auf http://127.0.0.1:8080/health"
  else
    warn "API laeuft noch nicht oder antwortet noch nicht auf /health"
  fi
fi

exit "$status"
