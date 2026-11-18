import os
import re
import sys

PORT = int(os.environ.get("PORT", "443"))

secret = os.environ.get("PROXY_SECRET", "").lower()

if not re.fullmatch(r"[0-9a-f]{32}", secret):
    print("ERROR: Invalid PROXY_SECRET")
    sys.exit(1)

USERS = {
    "friends": secret
}

MODES = {
    "classic": True,
    "secure": False,
    "tls": False,
}