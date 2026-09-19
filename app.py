import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file, session
import paho.mqtt.client as mqtt


MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
DEFAULT_PRINTER = os.environ.get("PRINTER_NAME", "LJ4000")
PRINTERS = {
    "LJ4000": {
        "label": "HP LaserJet 4000 DTN",
        "host": os.environ.get("PRINTER_HOST", "192.168.0.8"),
        "color": False,
        "power_topics": {
            "command": "print-gateway/printer/power/set",
            "state": "print-gateway/printer/power/state",
            "get": "print-gateway/printer/power/get",
        },
        "default_duplex": "DuplexNoTumble",
    },
    "HP477FDN": {
        "label": "HP Color LaserJet MFP M477fdn",
        "host": os.environ.get("COLOR_PRINTER_HOST", "192.168.0.7"),
        "color": True,
        "power_topics": {
            "command": "print-gateway/printer-color/power/set",
            "state": "print-gateway/printer-color/power/state",
            "get": "print-gateway/printer-color/power/get",
        },
        "default_duplex": "DuplexNoTumble",
    },
}
MQTT_HOST = os.environ.get("MQTT_HOST", "mqtt")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
PAGE_RANGES_RE = re.compile(r"^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")
DOCUMENT_TTL_SECONDS = int(os.environ.get("DOCUMENT_TTL_MINUTES", "30")) * 60
DOCUMENT_ROOT = Path(tempfile.gettempdir()) / "print-gateway-documents"
OFFICE_EXTENSIONS = {".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp"}
ACCEPTED_EXTENSIONS = OFFICE_EXTENSIONS | {".pdf"}

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
DOCUMENT_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)

power_lock = threading.Lock()
power = {
    name: {"state": "unknown", "connected": False, "updatedAt": None}
    for name in PRINTERS
}


def on_mqtt_connect(client, _userdata, _flags, reason_code, _properties):
    with power_lock:
        for state in power.values():
            state["connected"] = reason_code == 0
    if reason_code == 0:
        for config in PRINTERS.values():
            topics = config["power_topics"]
            client.subscribe(topics["state"], qos=1)
            client.publish(topics["get"], "state", qos=1)


def on_mqtt_disconnect(_client, _userdata, _disconnect_flags, _reason_code, _properties):
    with power_lock:
        for state in power.values():
            state["connected"] = False


def on_mqtt_message(_client, _userdata, message):
    state = message.payload.decode("utf-8", errors="replace").strip().lower()
    if state not in {"on", "off", "unavailable", "unknown"}:
        return
    printer = next(
        (name for name, config in PRINTERS.items() if message.topic == config["power_topics"]["state"]),
        None,
    )
    if not printer:
        return
    with power_lock:
        power[printer].update(state=state, updatedAt=datetime.now().isoformat(timespec="seconds"))


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


def remove_document(token: str | None) -> None:
    if token and re.fullmatch(r"[0-9a-f]{32}", token):
        shutil.rmtree(DOCUMENT_ROOT / token, ignore_errors=True)


def cleanup_documents() -> None:
    cutoff = time.time() - DOCUMENT_TTL_SECONDS
    for directory in DOCUMENT_ROOT.iterdir():
        try:
            if directory.is_dir() and directory.stat().st_mtime < cutoff:
                shutil.rmtree(directory, ignore_errors=True)
        except OSError:
            continue


def current_document(token: str) -> Path | None:
    if token != session.get("document_token") or not re.fullmatch(r"[0-9a-f]{32}", token):
        return None
    path = DOCUMENT_ROOT / token / "preview.pdf"
    try:
        if path.is_file() and path.stat().st_mtime < time.time() - DOCUMENT_TTL_SECONDS:
            remove_document(token)
            session.pop("document_token", None)
            return None
    except OSError:
        return None
    return path if path.is_file() else None


def pdf_page_count(path: Path) -> int | None:
    result = cups("qpdf", "--show-npages", str(path), timeout=10)
    try:
        return int(result.stdout.strip()) if result.returncode == 0 else None
    except ValueError:
        return None


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


def printer_config(name: str | None) -> tuple[str, dict]:
    selected = name or DEFAULT_PRINTER
    if selected not in PRINTERS:
        raise ValueError
    return selected, PRINTERS[selected]


def parse_jobs(output: str, printer: str) -> list[dict]:
    job_re = re.compile(rf"^{re.escape(printer)}-(\d+)\s+(\S+)\s+(\d+)\s+(.*)$")
    jobs = []
    for line in output.splitlines():
        match = job_re.match(line.strip())
        if not match:
            continue
        job_id, owner, size, submitted = match.groups()
        jobs.append(
            {
                "id": int(job_id),
                "name": f"{printer}-{job_id}",
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


def printer_reachable(host: str) -> bool:
    try:
        with socket.create_connection((host, 9100), timeout=0.7):
            return True
    except OSError:
        return False


def printer_status(printer: str) -> dict:
    printer, config = printer_config(printer)
    state = cups("lpstat", "-p", printer, "-l")
    active = cups("lpstat", "-o", printer)
    completed = cups("lpstat", "-W", "completed", "-o", printer)
    text = (state.stdout or state.stderr).strip()
    lower = text.lower()
    reachable = printer_reachable(config["host"])
    with power_lock:
        power_snapshot = {**power[printer], "managed": True}
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
        "printer": printer,
        "printerLabel": config["label"],
        "color": config["color"],
        "status": status,
        "label": label,
        "detail": text,
        "active": parse_jobs(active.stdout, printer),
        "completed": parse_jobs(completed.stdout, printer)[:8],
        "checkedAt": datetime.now().isoformat(timespec="seconds"),
        "power": {**power_snapshot, "printerReachable": reachable},
    }


@app.get("/")
def index():
    return render_template(
        "index.html",
        printers=[{"name": name, **config} for name, config in PRINTERS.items()],
        default_printer=DEFAULT_PRINTER,
        max_upload_mb=MAX_UPLOAD_MB,
        csrf_token=csrf_token(),
    )


@app.get("/api/status")
def api_status():
    try:
        return jsonify(printer_status(request.args.get("printer")))
    except ValueError:
        return jsonify(error="Nieznana drukarka."), 400


@app.post("/api/documents")
def api_prepare_document():
    denied = require_csrf()
    if denied:
        return denied
    cleanup_documents()
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(error="Wybierz dokument PDF lub Office."), 400
    original_name = Path(upload.filename).name
    extension = Path(original_name).suffix.lower()
    if extension not in ACCEPTED_EXTENSIONS:
        return jsonify(error="Obsługiwane są pliki PDF, Word, Excel, PowerPoint, OpenDocument i RTF."), 400

    old_token = session.pop("document_token", None)
    remove_document(old_token)
    token = uuid.uuid4().hex
    workdir = DOCUMENT_ROOT / token
    workdir.mkdir(mode=0o700)
    input_path = workdir / f"source{extension}"
    output_path = workdir / "preview.pdf"
    try:
        upload.save(input_path)
        if extension == ".pdf":
            with input_path.open("rb") as document:
                if document.read(5) != b"%PDF-":
                    raise ValueError("Wybrany plik nie jest prawidłowym dokumentem PDF.")
            input_path.replace(output_path)
        else:
            profile_uri = (workdir / "lo-profile").resolve().as_uri()
            env = {**os.environ, "HOME": str(workdir), "LANG": "pl_PL.UTF-8", "LC_ALL": "C.UTF-8"}
            conversion = subprocess.run(
                [
                    "libreoffice", "--headless", "--nologo", "--nodefault", "--nofirststartwizard",
                    "--nolockcheck", f"-env:UserInstallation={profile_uri}", "--convert-to", "pdf",
                    "--outdir", str(workdir), str(input_path),
                ],
                capture_output=True, text=True, timeout=90, env=env, check=False,
            )
            generated = workdir / "source.pdf"
            if conversion.returncode != 0 or not generated.is_file():
                detail = (conversion.stderr or conversion.stdout).strip()
                raise ValueError(f"Nie udało się przekonwertować dokumentu.{f' {detail}' if detail else ''}")
            generated.replace(output_path)
        pages = pdf_page_count(output_path)
        if not pages:
            raise ValueError("Wygenerowany PDF jest uszkodzony lub pusty.")
        input_path.unlink(missing_ok=True)
        shutil.rmtree(workdir / "lo-profile", ignore_errors=True)
        session["document_token"] = token
        return jsonify(
            ok=True,
            token=token,
            name=original_name,
            pages=pages,
            size=output_path.stat().st_size,
            converted=extension != ".pdf",
            previewUrl=f"/api/documents/{token}/preview",
        )
    except subprocess.TimeoutExpired:
        remove_document(token)
        return jsonify(error="Konwersja trwała zbyt długo i została przerwana."), 422
    except ValueError as error:
        remove_document(token)
        return jsonify(error=str(error)), 422
    except Exception:
        remove_document(token)
        app.logger.exception("Document preparation failed")
        return jsonify(error="Nie udało się przygotować podglądu dokumentu."), 500


@app.get("/api/documents/<token>/preview")
def api_document_preview(token: str):
    path = current_document(token)
    if not path:
        return jsonify(error="Podgląd wygasł. Wybierz dokument ponownie."), 404
    return send_file(path, mimetype="application/pdf", as_attachment=False, download_name="podglad.pdf", max_age=0)


@app.post("/api/print")
def api_print():
    denied = require_csrf()
    if denied:
        return denied

    try:
        printer, config = printer_config(request.form.get("printer"))
    except ValueError:
        return jsonify(error="Nieznana drukarka."), 400

    token = request.form.get("document_token", "")
    input_path = current_document(token)
    if not input_path:
        return jsonify(error="Podgląd wygasł. Wybierz dokument ponownie."), 400

    copies = request.form.get("copies", "1")
    resolution = request.form.get("resolution", "600")
    duplex = request.form.get("duplex", "None")
    number_up = request.form.get("number_up", "1")
    color_mode = request.form.get("color_mode", "color")
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
    if color_mode not in {"color", "monochrome"}:
        return jsonify(error="Nieprawidłowy tryb koloru."), 400

    original_name = request.form.get("document_name", "Dokument")
    title = re.sub(r"[^\w .()\-]+", "_", Path(original_name).stem, flags=re.UNICODE)[:80]
    with tempfile.TemporaryDirectory(prefix="print-") as workdir:
        selected_path = Path(workdir) / "selected.pdf"
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
            printer,
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
        if config["color"]:
            command.extend(["-o", "ColorModel=RGB" if color_mode == "color" else "ColorModel=Gray"])
        command.append(str(print_path))
        result = cups(*command, timeout=30)

    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        return jsonify(error=f"CUPS odrzucił zadanie: {message}"), 502

    match = re.search(r"request id is (\S+)", result.stdout)
    remove_document(token)
    session.pop("document_token", None)
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
    payload = request.get_json(silent=True) or {}
    action = payload.get("action")
    try:
        _printer, config = printer_config(payload.get("printer"))
    except ValueError:
        return jsonify(error="Nieznana drukarka."), 400
    if action not in {"on", "off"}:
        return jsonify(error="Nieprawidłowe polecenie zasilania."), 400
    with power_lock:
        connected = power[_printer]["connected"]
    if not connected:
        return jsonify(error="Brak połączenia z Home Assistantem."), 503
    result = mqtt_client.publish(config["power_topics"]["command"], action.upper(), qos=1)
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        return jsonify(error="Nie udało się przekazać polecenia do Home Assistanta."), 502
    return jsonify(ok=True, message="Polecenie zostało przekazane do Home Assistanta."), 202


@app.post("/api/jobs/<printer>/<int:job_id>/cancel")
def cancel_job(printer: str, job_id: int):
    denied = require_csrf()
    if denied:
        return denied
    try:
        printer, _config = printer_config(printer)
    except ValueError:
        return jsonify(error="Nieznana drukarka."), 400
    result = cups("cancel", f"{printer}-{job_id}")
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
