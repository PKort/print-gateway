import base64
import hashlib
import os
from pathlib import Path

username = os.environ["MQTT_NEW_USERNAME"]
password = os.environ["MQTT_NEW_PASSWORD"]
line = next(line for line in Path("/usr/share/mqtt/config-mqtt/auth.txt").read_text().splitlines() if line.startswith(username + ":"))
scheme, algorithm, iterations, salt, expected = line.split(":", 1)[1].split("$")
actual = hashlib.pbkdf2_hmac(algorithm, password.encode(), base64.b64decode(salt), int(iterations), dklen=len(base64.b64decode(expected)))
print("valid" if scheme == "PBKDF2" and actual == base64.b64decode(expected) else "invalid")
