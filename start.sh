#!/bin/sh
PORT="${PORT:-8443}"

# إذا ما حطيت SECRET بالـVariables، يسوي واحد جديد ويطبعه باللوگ
if [ -z "$SECRET" ]; then
  FAKE_DOMAIN="${FAKE_DOMAIN:-google.com}"
  SECRET=$(/mtg generate-secret "$FAKE_DOMAIN")
  echo "=============================================="
  echo "SECRET (انسخه وحطه بالـVariables حتى ما يتغير): $SECRET"
  echo "=============================================="
fi

cat > /config.toml <<CONF
secret = "$SECRET"
bind-to = "0.0.0.0:$PORT"
prefer-ip = "prefer-ipv4"
CONF

exec /mtg run /config.toml
