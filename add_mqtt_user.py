import base64
import hashlib
import os
import shutil
from pathlib import Path

path = Path("/usr/share/mqtt/config-mqtt/auth.txt")
username = os.environ["MQTT_NEW_USERNAME"]
password = os.environ["MQTT_NEW_PASSWORD"]
lines = path.read_text(encoding="utf-8").splitlines()
if any(line.startswith(username + ":") for line in lines):
    raise SystemExit("user already exists")
salt = os.urandom(16)
digest = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 100_000, dklen=64)
encoded = "PBKDF2$sha512$100000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()
backup = path.with_name("auth.txt.before-print-gateway")
if not backup.exists():
    shutil.copy2(path, backup)
with path.open("a", encoding="utf-8") as handle:
    handle.write(f"{username}:{encoded}\n")
