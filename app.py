import os
import re
import socket
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session
import paho.mqtt.client as mqtt


PRINTER = os.environ.get("PRINTER_NAME", "LJ4000")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
PRINTER_HOST = os.environ.get("PRINTER_HOST", "192.168.0.8")
MQTT_HOST = os.environ.get("MQTT_HOST", "mqtt")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
POWER_COMMAND_TOPIC = "print-gateway/printer/power/set"
POWER_STATE_TOPIC = "print-gateway/printer/power/state"
POWER_GET_TOPIC = "print-gateway/printer/power/get"
JOB_RE = re.compile(rf"^{re.escape(PRINTER)}-(\d+)\s+(\S+)\s+(\d+)\s+(.*)$")
PAGE_RANGES_RE = re.compile(r"^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

power_lock = threading.Lock()
power = {"state": "unknown", "connected": False, "updatedAt": None}


def on_mqtt_connect(client, _userdata, _flags, reason_code, _properties):
    with power_lock:
        power["connected"] = reason_code == 0
    if reason_code == 0:
        client.subscribe(POWER_STATE_TOPIC, qos=1)
        client.publish(POWER_GET_TOPIC, "state", qos=1)


def on_mqtt_disconnect(_client, _userdata, _disconnect_flags, _reason_code, _properties):
    with power_lock:
        power["connected"] = False


def on_mqtt_message(_client, _userdata, message):
    state = message.payload.decode("utf-8", errors="replace").strip().lower()
    if state not in {"on", "off", "unavailable", "unknown"}:
        return
    with power_lock:
        power.update(state=state, updatedAt=datetime.now().isoformat(timespec="seconds"))


mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="print-gateway")
mqtt_client.username_pw_set(os.environ["MQTT_USERNAME"], os.environ["MQTT_PASSWORD"])
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_disconnect = on_mqtt_disconnect
mqtt_client.on_message = on_mqtt_message
mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)
mqtt_client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
mqtt_client.loop_start()


def cups(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    env = {**os.environ, "LANG": "C", "LC_ALL": "C"}
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = os.urandom(24).hex()
        session["csrf_token"] = token
    return token


def require_csrf():
    if request.headers.get("X-CSRF-Token") != session.get("csrf_token"):
        return jsonify(error="Sesja wygasła. Odśwież stronę i spróbuj ponownie."), 403
    return None


def parse_jobs(output: str) -> list[dict]:
    jobs = []
    for line in output.splitlines():
        match = JOB_RE.match(line.strip())
        if not match:
            continue
        job_id, owner, size, submitted = match.groups()
        jobs.append(
            {
                "id": int(job_id),
                "name": f"{PRINTER}-{job_id}",
                "owner": owner,
                "size": int(size),
                "submitted": submitted,
            }
        )
    return jobs


def normalize_page_ranges(value: str) -> str | None:
    compact = re.sub(r"\s+", "", value)
    if not compact:
        return None
    if not PAGE_RANGES_RE.fullmatch(compact):
        raise ValueError
    for part in compact.split(","):
        bounds = [int(number) for number in part.split("-")]
        if any(number < 1 or number > 9999 for number in bounds):
            raise ValueError
        if len(bounds) == 2 and bounds[0] > bounds[1]:
            raise ValueError
    return compact


def printer_reachable() -> bool:
    try:
        with socket.create_connection((PRINTER_HOST, 9100), timeout=0.7):
            return True
    except OSError:
        return False


def printer_status() -> dict:
    state = cups("lpstat", "-p", PRINTER, "-l")
    active = cups("lpstat", "-o", PRINTER)
    completed = cups("lpstat", "-W", "completed", "-o", PRINTER)
    text = (state.stdout or state.stderr).strip()
    lower = text.lower()
    with power_lock:
        power_snapshot = dict(power)
    reachable = printer_reachable() if power_snapshot["state"] == "on" else False
    if power_snapshot["state"] == "off":
        status = "off"
        label = "Wyłączona"
    elif power_snapshot["state"] == "on" and not reachable:
        status = "warming"
        label = "Uruchamia się"
    elif state.returncode != 0:
        status = "error"
        label = "CUPS niedostępny"
    elif " is idle" in lower:
        status = "ready"
        label = "Gotowa"
    elif " now printing" in lower:
        status = "printing"
        label = "Drukowanie"
    else:
        status = "warning"
        label = "Wymaga uwagi"
    return {
        "printer": PRINTER,
        "status": status,
        "label": label,
        "detail": text,
        "active": parse_jobs(active.stdout),
        "completed": parse_jobs(completed.stdout)[:8],
        "checkedAt": datetime.now().isoformat(timespec="seconds"),
        "power": {**power_snapshot, "printerReachable": reachable},
    }


@app.get("/")
def index():
    return render_template(
        "index.html",
        printer=PRINTER,
        max_upload_mb=MAX_UPLOAD_MB,
        csrf_token=csrf_token(),
    )


@app.get("/api/status")
def api_status():
    return jsonify(printer_status())


@app.post("/api/print")
def api_print():
    denied = require_csrf()
    if denied:
        return denied

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(error="Wybierz plik PDF."), 400

    copies = request.form.get("copies", "1")
    resolution = request.form.get("resolution", "600")
    duplex = request.form.get("duplex", "None")
    number_up = request.form.get("number_up", "1")
    try:
        page_ranges = normalize_page_ranges(request.form.get("page_ranges", ""))
    except ValueError:
        return jsonify(error="Nieprawidłowy zakres stron. Użyj np. 1-3, 5, 8-10."), 400
    if not copies.isdigit() or not 1 <= int(copies) <= 20:
        return jsonify(error="Liczba kopii musi mieścić się w zakresie 1–20."), 400
    if resolution not in {"300", "600"}:
        return jsonify(error="Nieprawidłowa rozdzielczość."), 400
    if duplex not in {"None", "DuplexNoTumble", "DuplexTumble"}:
        return jsonify(error="Nieprawidłowe ustawienie druku dwustronnego."), 400
    if number_up not in {"1", "2", "4", "6", "9", "16"}:
        return jsonify(error="Nieprawidłowa liczba stron na arkuszu."), 400

    original_name = Path(upload.filename).name
    title = re.sub(r"[^\w .()\-]+", "_", Path(original_name).stem, flags=re.UNICODE)[:80]
    with tempfile.TemporaryDirectory(prefix="print-") as workdir:
        input_path = Path(workdir) / "input.pdf"
        selected_path = Path(workdir) / "selected.pdf"
        upload.save(input_path)
        with input_path.open("rb") as document:
            signature = document.read(5)
        if signature != b"%PDF-":
            return jsonify(error="Wybrany plik nie jest prawidłowym dokumentem PDF."), 400

        print_path = input_path
        if page_ranges:
            selection = cups(
                "qpdf",
                str(input_path),
                "--pages",
                ".",
                page_ranges,
                "--",
                str(selected_path),
                timeout=30,
            )
            if selection.returncode != 0:
                return jsonify(error="Zakres wykracza poza dokument lub PDF jest uszkodzony."), 400
            print_path = selected_path

        command = [
            "lp",
            "-d",
            PRINTER,
            "-t",
            title or "Dokument PDF",
            "-n",
            copies,
            "-o",
            "PageSize=A4",
            "-o",
            f"Resolution={resolution}dpi",
            "-o",
            f"Duplex={duplex}",
            "-o",
            f"number-up={number_up}",
            "-o",
            "number-up-layout=lrtb",
        ]
        command.append(str(print_path))
        result = cups(*command, timeout=30)

    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        return jsonify(error=f"CUPS odrzucił zadanie: {message}"), 502

    match = re.search(r"request id is (\S+)", result.stdout)
    return jsonify(
        ok=True,
        job=match.group(1) if match else None,
        message="Dokument został dodany do kolejki.",
    )


@app.post("/api/power")
def api_power():
    denied = require_csrf()
    if denied:
        return denied
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in {"on", "off"}:
        return jsonify(error="Nieprawidłowe polecenie zasilania."), 400
    with power_lock:
        connected = power["connected"]
    if not connected:
        return jsonify(error="Brak połączenia z Home Assistantem."), 503
    result = mqtt_client.publish(POWER_COMMAND_TOPIC, action.upper(), qos=1)
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        return jsonify(error="Nie udało się przekazać polecenia do Home Assistanta."), 502
    return jsonify(ok=True, message="Polecenie zostało przekazane do Home Assistanta."), 202


@app.post("/api/jobs/<int:job_id>/cancel")
def cancel_job(job_id: int):
    denied = require_csrf()
    if denied:
        return denied
    result = cups("cancel", f"{PRINTER}-{job_id}")
    if result.returncode != 0:
        return jsonify(error=(result.stderr or "Nie udało się anulować zadania.").strip()), 400
    return jsonify(ok=True)


@app.get("/healthz")
def health():
    result = cups("lpstat", "-r", timeout=5)
    return (jsonify(ok=True), 200) if result.returncode == 0 else (jsonify(ok=False), 503)


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error=f"Plik jest większy niż {MAX_UPLOAD_MB} MB."), 413
