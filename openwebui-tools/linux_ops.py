"""
title: Operazioni Linux
author: homelab
version: 0.1.0
description: Diagnostica rete e operazioni di sistema per il homelab. Endpoint attivi richiedono confirmed=true dopo conferma esplicita dell'utente.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get("LINUX_OPS_API_BASE", "http://host.docker.internal:11436")

# Allowlist: only these systemd units can be restarted or have their logs read.
ALLOWED_SERVICES = frozenset(
    s.strip()
    for s in os.environ.get(
        "LINUX_OPS_ALLOWED_SERVICES", "llama-server,server-api,linux-ops,docker"
    ).split(",")
    if s.strip()
)


def _check_service(servizio: str) -> str | None:
    """Return an error message if the service is not allowlisted, else None."""
    if servizio not in ALLOWED_SERVICES:
        return (
            f"Servizio '{servizio}' non consentito. "
            f"Consentiti: {', '.join(sorted(ALLOWED_SERVICES))}"
        )
    return None


def _get(path: str) -> dict:
    """GET JSON dal backend linux-ops; ritorna {"error": ...} se non raggiungibile."""
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, body: dict) -> dict:
    """POST JSON al backend linux-ops; ritorna {"error": ...} se non raggiungibile."""
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}


class Tools:
    """Tool di diagnostica; le azioni richiedono confirmed=true e un servizio in allowlist."""

    def __init__(self) -> None:
        pass

    async def stato_rete(self) -> str:
        """
        Mostra lo stato delle interfacce di rete: indirizzi IP, rete WiFi connessa, route di default, stato Tailscale.
        Usa questo tool quando l'utente chiede lo stato della rete, l'IP del server, la connessione WiFi, o Tailscale.
        :return: stato rete in formato testuale
        """
        d = _get("/net/status")
        if "error" in d:
            return f"Errore: {d['error']}"
        return (
            f"Interfacce:\n{d.get('interfaces', 'n/a')}\n"
            f"Route default: {d.get('default_route', 'n/a')}\n"
            f"WiFi SSID: {d.get('wifi_ssid', 'n/a')} | Segnale: {d.get('wifi_signal', 'n/a')}\n"
            f"Tailscale:\n{d.get('tailscale', 'n/a')}"
        )

    async def test_internet(self) -> str:
        """
        Verifica se il server ha accesso a internet pingando 1.1.1.1, 8.8.8.8 e testando HTTPS.
        Usa questo tool quando l'utente chiede se internet funziona o per diagnosticare assenza di rete.
        :return: risultato check connettività
        """
        d = _get("/net/internet")
        if "error" in d:
            return f"Errore: {d['error']}"
        status = "ONLINE" if d.get("internet") else "OFFLINE"
        checks = d.get("checks", {})
        lines = [f"Internet: {status}"]
        for host, ok in checks.items():
            lines.append(f"  {host}: {'raggiungibile' if ok else 'non raggiungibile'}")
        return "\n".join(lines)

    async def ping(self, host: str) -> str:
        """
        Esegue un ping verso un host specifico per verificarne la raggiungibilità.
        :param host: hostname o indirizzo IP da pingare
        :return: risultato ping
        """
        d = _get(f"/net/ping?host={urllib.parse.quote(host)}")
        if "error" in d:
            return f"Errore: {d['error']}"
        reachable = "raggiungibile" if d.get("reachable") else "non raggiungibile"
        return f"Ping verso {host}: {reachable}\n{d.get('output', '')}"

    async def leggi_log(self, servizio: str, righe: int = 20) -> str:
        """
        Mostra le ultime righe del log di un servizio systemd.
        Servizi consentiti: quelli in allowlist (LINUX_OPS_ALLOWED_SERVICES).
        :param servizio: nome del servizio (es: llama-server, docker)
        :param righe: numero di righe da mostrare (default 20, max 100)
        :return: ultime righe del log
        """
        if (err := _check_service(servizio)) is not None:
            return f"Errore: {err}"
        righe = max(1, min(int(righe), 100))
        d = _get(f"/sys/log?service={urllib.parse.quote(servizio)}&n={righe}")
        if "error" in d:
            return f"Errore: {d['error']}"
        return f"Log {servizio} (ultime {d.get('lines', righe)} righe):\n{d.get('log', '(vuoto)')}"

    async def lista_directory(self, path: str) -> str:
        """
        Mostra il contenuto di una directory del server.
        Sono consentiti solo i percorsi in allowlist sul backend (es. /path/to/data, /var/log).
        :param path: percorso della directory
        :return: elenco file e directory
        """
        d = _get(f"/fs/ls?path={urllib.parse.quote(path)}")
        if "error" in d:
            return f"Errore: {d['error']}"
        return f"Contenuto di {d.get('path', path)}:\n{d.get('listing', '(vuoto)')}"

    async def riconnetti_wifi(self, confirmed: bool = False) -> str:
        """
        Riconnette il server alla rete WiFi (interface wlo1 down poi up, poi netplan apply).
        IMPORTANTE: chiama prima con confirmed=False per mostrare il comando all'utente.
        Richiama con confirmed=True SOLO dopo che l'utente ha scritto esplicitamente "confermo".
        :param confirmed: False = mostra il comando senza eseguire; True = esegue (solo dopo conferma utente)
        :return: preview del comando o risultato dell'esecuzione
        """
        d = _post("/net/reconnect", {"confirmed": confirmed})
        if "error" in d:
            return f"Errore: {d['error']}"
        if d.get("conferma_richiesta"):
            return f"{d['messaggio']}\nComando: {d['comando']}"
        return f"Eseguito (exit {d.get('exit_code', '?')}): {d.get('output', 'ok')}"

    async def riavvia_servizio(self, servizio: str, confirmed: bool = False) -> str:
        """
        Riavvia un servizio systemd del server.
        IMPORTANTE: chiama prima con confirmed=False per mostrare il comando all'utente.
        Richiama con confirmed=True SOLO dopo che l'utente ha scritto esplicitamente "confermo".
        Servizi riavviabili: solo quelli in allowlist (LINUX_OPS_ALLOWED_SERVICES).
        :param servizio: nome del servizio da riavviare
        :param confirmed: False = mostra il comando senza eseguire; True = esegue (solo dopo conferma utente)
        :return: preview del comando o risultato dell'esecuzione
        """
        if (err := _check_service(servizio)) is not None:
            return f"Errore: {err}"
        d = _post("/sys/restart", {"service": servizio, "confirmed": confirmed})
        if "error" in d:
            return f"Errore: {d['error']}"
        if d.get("conferma_richiesta"):
            return f"{d['messaggio']}\nComando: {d['comando']}"
        return f"Servizio '{servizio}' riavviato (exit {d.get('exit_code', '?')}): {d.get('output', 'ok')}"
