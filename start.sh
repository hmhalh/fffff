#!/usr/bin/env bash
# Self-healing MTProto (FakeTLS) proxy for Railway.
# The container never exits: this script keeps mtg alive and restarts it
# by itself, so Railway's Restart Policy is not needed.
set -u

LISTEN_PORT="${MTG_PORT:-${PORT:-3128}}"
FRONT_DOMAIN="${FRONT_DOMAIN:-www.cloudflare.com}"
RESTART_MINUTES="${RESTART_MINUTES:-360}"   # scheduled restart of mtg (0 = off)
CHECK_EVERY="${CHECK_EVERY:-60}"            # seconds between health checks
DC_CHECK="${DC_CHECK:-1}"                   # 1 = check outbound path to Telegram
PREFER_IP="${PREFER_IP:-prefer-ipv4}"
MTG_BIN="${MTG_BIN:-/usr/local/bin/mtg}"
CONF="${CONF:-/tmp/mtg.toml}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

tcp_ok() { timeout 4 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }

dc_ok() {
  local ip
  for ip in 149.154.167.51 149.154.167.91 149.154.175.53 149.154.175.100 91.108.56.130; do
    tcp_ok "$ip" 443 && return 0
  done
  return 1
}

# ---------- secret (stable across restarts and redeploys) ----------
make_secret() {
  local dom key s
  dom=$(printf '%s' "$FRONT_DOMAIN" | od -An -tx1 | tr -d ' \n')
  if [ -n "${SECRET:-}" ]; then
    s=$(printf '%s' "$SECRET" | tr 'A-F' 'a-f')
    if printf '%s' "$s" | grep -Eq '^[0-9a-f]{32}$'; then
      echo "ee${s}${dom}"; return
    elif printf '%s' "$s" | grep -Eq '^dd[0-9a-f]{32}$'; then
      echo "ee${s#dd}${dom}"; return
    fi
    echo "$SECRET"; return
  fi
  local seed="${SEED:-${RAILWAY_SERVICE_ID:-${RAILWAY_PROJECT_ID:-mtproxy-default-seed}}}"
  key=$(printf '%s' "mtproxy:${seed}" | sha256sum | cut -c1-32)
  echo "ee${key}${dom}"
}
SECRET_FULL=$(make_secret)

SAFE=0
build_config() {
  {
    echo "secret = \"${SECRET_FULL}\""
    echo "bind-to = \"0.0.0.0:${LISTEN_PORT}\""
    if [ "$SAFE" != 1 ]; then
      [ -n "$PREFER_IP" ] && echo "prefer-ip = \"${PREFER_IP}\""
      [ -n "${PUBLIC_IPV4:-}" ] && echo "public-ipv4 = \"${PUBLIC_IPV4}\""
    fi
  } > "$CONF"
}

print_link() {
  local host="${RAILWAY_TCP_PROXY_DOMAIN:-YOUR_HOST}"
  local port="${RAILWAY_TCP_PROXY_PORT:-YOUR_PORT}"
  log "LINK: https://t.me/proxy?server=${host}&port=${port}&secret=${SECRET_FULL}"
}

MTG_PID=0
cleanup() {
  [ "$MTG_PID" -gt 0 ] && kill "$MTG_PID" 2>/dev/null
  exit 0
}
trap cleanup TERM INT

log "boot: listen=${LISTEN_PORT} domain=${FRONT_DOMAIN} restart_every=${RESTART_MINUTES}min"
print_link

FAST=0
LAST_DC_RESTART=0

while true; do
  build_config
  START=$(date +%s)
  "$MTG_BIN" run "$CONF" &
  MTG_PID=$!
  log "mtg started (pid=${MTG_PID}, safe_mode=${SAFE})"
  LOCAL_FAIL=0
  DC_FAIL=0
  REASON=""

  while kill -0 "$MTG_PID" 2>/dev/null; do
    sleep "$CHECK_EVERY" & wait $!
    kill -0 "$MTG_PID" 2>/dev/null || break
    NOW=$(date +%s)
    UP=$((NOW - START))

    # 1) scheduled restart (cleans up any silent hang)
    if [ "$RESTART_MINUTES" -gt 0 ] && [ "$UP" -ge $((RESTART_MINUTES * 60)) ]; then
      REASON="scheduled restart"
      break
    fi

    # 2) is the proxy port still answering?
    if [ "$UP" -gt 20 ]; then
      if tcp_ok 127.0.0.1 "$LISTEN_PORT"; then
        LOCAL_FAIL=0
      else
        LOCAL_FAIL=$((LOCAL_FAIL + 1))
        log "port check failed (${LOCAL_FAIL}/3)"
        if [ "$LOCAL_FAIL" -ge 3 ]; then
          REASON="port not answering"
          break
        fi
      fi
    fi

    # 3) can we still reach Telegram servers? (max one restart per 10 min)
    if [ "$DC_CHECK" = 1 ] && [ "$UP" -gt 20 ]; then
      if dc_ok; then
        DC_FAIL=0
      else
        DC_FAIL=$((DC_FAIL + 1))
        log "cannot reach Telegram servers (${DC_FAIL}/3)"
        if [ "$DC_FAIL" -ge 3 ] && [ $((NOW - LAST_DC_RESTART)) -ge 600 ]; then
          LAST_DC_RESTART=$NOW
          REASON="no route to Telegram"
          break
        fi
      fi
    fi
  done

  if [ -n "$REASON" ]; then
    log "restarting mtg: ${REASON}"
    kill "$MTG_PID" 2>/dev/null
  fi
  wait "$MTG_PID" 2>/dev/null
  CODE=$?
  UP=$(( $(date +%s) - START ))
  log "mtg stopped (exit=${CODE} after ${UP}s)"
  MTG_PID=0

  # if mtg dies right after start again and again, fall back to a minimal config
  if [ "$UP" -lt 15 ]; then FAST=$((FAST + 1)); else FAST=0; fi
  if [ "$FAST" -ge 3 ] && [ "$SAFE" = 0 ]; then
    SAFE=1
    log "mtg keeps failing at startup -> safe mode (minimal config)"
  fi

  DELAY=3
  if [ "$FAST" -gt 0 ]; then
    DELAY=$((FAST * 5))
    [ "$DELAY" -gt 30 ] && DELAY=30
  fi
  sleep "$DELAY" & wait $!
done
