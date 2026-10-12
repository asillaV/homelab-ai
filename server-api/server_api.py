#!/usr/bin/env python3
"""
Micro-API read-only per il homelab.
Espone endpoint JSON consumati dai tool di Open WebUI.
Configurazione via variabili d'ambiente (vedi .env.example).
Nessuna operazione distruttiva — solo lettura.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# Per servire anche i container Docker (host.docker.internal) il bind deve
# essere raggiungibile dal bridge Docker: restringilo con un firewall / VPN.
LISTEN = os.environ.get("SERVER_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("SERVER_API_PORT", "11435"))

# NetAlertX GraphQL
NAX_URL = os.environ.get("NETALERTX_URL", "http://127.0.0.1:20212/graphql")
NAX_TOKEN = os.environ.get("NETALERTX_TOKEN", "")

PRESENCE_CMD = os.environ.get("PRESENCE_CMD", "")
MONITORED_SERVICES = [
    s.strip()
    for s in os.environ.get(
        "MONITORED_SERVICES", "llama-server,server-api,docker,tailscaled"
    ).split(",")
    if re.fullmatch(r"[A-Za-z0-9@_.-]+", s.strip())  # solo nomi di unit sicuri
]


def run_cmd(cmd: str, timeout: int = 10) -> str:
    """Esegue un comando shell e ritorna stdout (stringa)."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return "(timeout)"
    except Exception as e:
        return f"(errore: {e})"


def get_status(compact: bool = False) -> dict:
    """Stato generale del server."""
    uptime = run_cmd("uptime -p")
    load = run_cmd("cat /proc/loadavg")
    mem = run_cmd("free -h | awk '/^Mem:/{print $2, $3, $4}'")
    mem_parts = mem.split()
    disk_root = run_cmd("df -h / | awk 'NR==2{print $2, $3, $4, $5}'")
    disk_srv = run_cmd("df -h /srv | awk 'NR==2{print $2, $3, $4, $5}'")
    cpu_temp = run_cmd("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null")
    try:
        cpu_temp = f"{int(cpu_temp) / 1000:.1f}°C"
    except (ValueError, TypeError):
        cpu_temp = "n/a"

    if compact:
        load_1m = load.split()[0] if load else "?"
        return {
            "up": uptime,
            "load": load_1m,
            "mem_used": mem_parts[1] if len(mem_parts) > 1 else "?",
            "mem_total": mem_parts[0] if len(mem_parts) > 0 else "?",
            "disk_root": disk_root.split()[3] if disk_root else "?",
            "disk_srv": disk_srv.split()[3] if disk_srv else "?",
            "cpu_temp": cpu_temp,
        }
    return {
        "uptime": uptime,
        "load_avg": load,
        "memory": {
            "total": mem_parts[0] if len(mem_parts) > 0 else "?",
            "used": mem_parts[1] if len(mem_parts) > 1 else "?",
            "free": mem_parts[2] if len(mem_parts) > 2 else "?",
        },
        "disk_root": disk_root,
        "disk_srv": disk_srv,
        "cpu_temp": cpu_temp,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_presence(compact: bool = False) -> dict:
    """Chi è in casa (via script esterno configurato in PRESENCE_CMD)."""
    if not PRESENCE_CMD:
        return {"presence": "non configurato (PRESENCE_CMD)"}
    raw = run_cmd(PRESENCE_CMD, timeout=15)
    if compact:
        return {"presence": raw}
    return {"presence": raw, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}


def get_devices(compact: bool = False) -> dict:
    """Dispositivi di rete online (via NetAlertX GraphQL)."""
    query = ('query { devices(options:{status:"all_devices",page:1,limit:500}) '
             '{ devices { devName devMac devVendor devLastIP '
             'devPresentLastScan devFirstConnection devLastConnection } } }')
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(NAX_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if NAX_TOKEN:
        req.add_header("Authorization", "Bearer " + NAX_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        all_devs = data["devices"]["devices"]
        online = [d for d in all_devs if d.get("devPresentLastScan", 0) == 1]
        if compact:
            return {
                "online_count": len(online),
                "devices": [{"n": d["devName"], "ip": d["devLastIP"]} for d in online],
            }
        return {
            "online": [
                {"name": d["devName"], "mac": d["devMac"],
                 "ip": d["devLastIP"], "vendor": d["devVendor"]}
                for d in online
            ],
            "online_count": len(online),
            "total_count": len(all_devs),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        return {"error": str(e), "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}


def get_containers(compact: bool = False) -> dict:
    """Stato container Docker."""
    raw = run_cmd(
        'docker ps --format "{{.Names}}|{{.Status}}|{{.Image}}" 2>/dev/null',
        timeout=10,
    )
    containers = []
    for line in raw.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            containers.append(
                {"name": parts[0], "status": parts[1], "image": parts[2]}
            )
    if compact:
        unhealthy = [c for c in containers if not c["status"].startswith("Up")]
        return {
            "count": len(containers),
            "unhealthy": [c["name"] for c in unhealthy] or "tutti ok",
        }
    return {"containers": containers, "count": len(containers)}


def get_services(compact: bool = False) -> dict:
    """Stato dei servizi principali del server (media, rete, sistema)."""
    services = {}
    for svc in MONITORED_SERVICES:
        services[svc] = run_cmd(f"systemctl is-active {shlex.quote(svc)} 2>/dev/null")
    if compact:
        down = [k for k, v in services.items() if v != "active"]
        return {"down": down or "tutti attivi"}
    return {"services": services, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}


# ── Routes ──────────────────────────────────────────────────────────────

ROUTES = {
    "/status": get_status,
    "/presence": get_presence,
    "/devices": get_devices,
    "/containers": get_containers,
    "/services": get_services,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        compact = qs.get("format", [""])[0] == "compact"

        if path == "/":
            data = {
                "server": "homelab server-api",
                "endpoints": list(ROUTES.keys()),
                "note": "read-only. Aggiungi ?format=compact per risposte brevi.",
            }
            self._json_response(200, data)
            return

        handler = ROUTES.get(path)
        if handler:
            try:
                data = handler(compact=compact)
                self._json_response(200, data)
            except Exception as e:
                self._json_response(500, {"error": str(e)})
        else:
            self._json_response(404, {"error": f"endpoint '{path}' non trovato"})

    def _json_response(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Silenzioso in produzione
        pass


if __name__ == "__main__":
    server = HTTPServer((LISTEN, PORT), Handler)
    print(f"server-api in ascolto su {LISTEN}:{PORT}")
    print(f"Endpoint: {', '.join(ROUTES.keys())}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nChiusura.")
        server.server_close()
