import http.cookiejar
import json
import re
import urllib.request

cookies = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
html = opener.open("http://127.0.0.1:3030/", timeout=5).read().decode()
csrf = re.search(r"csrf:\s*(\"[a-f0-9]+\")", html)
if not csrf:
    raise SystemExit("csrf token not found")
token = json.loads(csrf.group(1))
request = urllib.request.Request(
    "http://127.0.0.1:3030/api/power",
    data=json.dumps({"action": "on"}).encode(),
    headers={"Content-Type": "application/json", "X-CSRF-Token": token},
    method="POST",
)
with opener.open(request, timeout=5) as response:
    print(response.status, response.read().decode())
