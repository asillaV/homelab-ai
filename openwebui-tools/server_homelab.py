"""
title: Homelab Server
author: homelab
version: 0.1.0
description: Strumenti read-only per monitorare il homelab (stato, presenza, dispositivi, container)
"""

import json
import os
import urllib.request

API_BASE = os.environ.get("SERVER_API_BASE", "http://host.docker.internal:11435")


def _get(endpoint: str) -> dict:
    """GET JSON dal server-api sull'host; solleva eccezione se non raggiungibile."""
    url = f"{API_BASE}{endpoint}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


class Tools:
    """Tool read-only esposti al modello (tool calling di Open WebUI)."""

    def __init__(self) -> None:
        pass

    async def server_status(self) -> str:
        """
        Mostra lo stato del server: uptime, carico CPU, memoria RAM, spazio disco e temperatura.
        Usa questo tool quando l'utente chiede come sta il server, se è tutto ok, o vuole info sul sistema.
        :return: stato del server in formato testuale
        """
        try:
            d = _get("/status")
            return (
                f"Homelab — {d['timestamp']}\n"
                f"Uptime: {d['uptime']}\n"
                f"Load: {d['load_avg']}\n"
                f"RAM: totale {d['memory']['total']}, usata {d['memory']['used']}, libera {d['memory']['free']}\n"
                f"Disco /: {d['disk_root']}\n"
                f"Disco /srv: {d['disk_srv']}\n"
                f"Temperatura CPU: {d['cpu_temp']}"
            )
        except Exception as e:
            return f"Errore: {e}"

    async def chi_e_in_casa(self) -> str:
        """
        Controlla chi è in casa e chi è fuori, basandosi sulla rilevazione dei telefoni sulla rete WiFi.
        Usa questo tool quando l'utente chiede chi c'è in casa, chi è presente, chi è fuori, o dove sono le persone.
        :return: elenco persone in casa e fuori con durate
        """
        try:
            d = _get("/presence")
            return d.get("presence", "Nessun dato disponibile")
        except Exception as e:
            return f"Errore: {e}"

    async def dispositivi_rete(self) -> str:
        """
        Elenca tutti i dispositivi attualmente online sulla rete locale (WiFi e cablati).
        Usa questo tool quando l'utente chiede quanti dispositivi sono connessi, cosa c'è in rete, o dettagli sui device.
        :return: lista dispositivi online con nome, IP, MAC e produttore
        """
        try:
            d = _get("/devices")
            if "error" in d:
                return f"Errore: {d['error']}"
            lines = [f"Dispositivi online: {d['online_count']} (su {d['total_count']} totali)\n"]
            for dev in d["online"]:
                lines.append(f"  - {dev['name']} | {dev['ip']} ({dev['vendor']})")
            return "\n".join(lines)
        except Exception as e:
            return f"Errore: {e}"

    async def stato_container(self) -> str:
        """
        Mostra lo stato di tutti i container Docker in esecuzione sul server.
        Usa questo tool quando l'utente chiede dei container, dei servizi Docker, o se un servizio specifico è attivo.
        :return: lista container con nome, stato e immagine
        """
        try:
            d = _get("/containers")
            lines = [f"Container Docker attivi: {d['count']}\n"]
            for c in d["containers"]:
                lines.append(f"  - {c['name']}: {c['status']} [{c['image']}]")
            return "\n".join(lines)
        except Exception as e:
            return f"Errore: {e}"

    async def stato_servizi(self) -> str:
        """
        Verifica se i servizi principali del server sono attivi (llama-server, Docker, Tailscale, presence).
        Usa questo tool quando l'utente chiede se i servizi funzionano o vuole un health check.
        :return: stato dei servizi principali
        """
        try:
            d = _get("/services")
            lines = [f"Servizi — {d['timestamp']}\n"]
            for svc, status in d["services"].items():
                icon = "OK" if status == "active" else "KO"
                lines.append(f"  [{icon}] {svc}: {status}")
            return "\n".join(lines)
        except Exception as e:
            return f"Errore: {e}"
