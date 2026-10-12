"""Offline tests for the Open WebUI tools: backend unreachable, allowlist, formatting."""
import asyncio

import linux_ops
import server_homelab


def run(coro):
    return asyncio.run(coro)


def _unreachable(*args, **kwargs):
    raise OSError("connection refused")


def test_server_tool_reports_error_when_api_unreachable(monkeypatch):
    monkeypatch.setattr(server_homelab.urllib.request, "urlopen", _unreachable)
    tools = server_homelab.Tools()
    for fn in (tools.server_status, tools.chi_e_in_casa, tools.dispositivi_rete,
               tools.stato_container, tools.stato_servizi):
        out = run(fn())
        assert out.startswith("Errore:") and "connection refused" in out


def test_server_tool_formats_container_list(monkeypatch):
    monkeypatch.setattr(
        server_homelab, "_get",
        lambda ep: {"count": 1, "containers": [{"name": "web", "status": "Up 1h", "image": "nginx"}]},
    )
    out = run(server_homelab.Tools().stato_container())
    assert "web: Up 1h [nginx]" in out


def test_linux_ops_reports_error_when_api_unreachable(monkeypatch):
    monkeypatch.setattr(linux_ops.urllib.request, "urlopen", _unreachable)
    tools = linux_ops.Tools()
    assert run(tools.stato_rete()).startswith("Errore:")
    assert run(tools.test_internet()).startswith("Errore:")
    assert run(tools.riconnetti_wifi()).startswith("Errore:")


def test_restart_rejects_service_outside_allowlist(monkeypatch):
    calls = []
    monkeypatch.setattr(linux_ops, "_post", lambda *a, **k: calls.append(a) or {})
    out = run(linux_ops.Tools().riavvia_servizio("sshd", confirmed=True))
    assert "non consentito" in out
    assert calls == []  # the backend is never contacted


def test_restart_allowed_service_without_confirmation_returns_preview(monkeypatch):
    sent = {}

    def fake_post(path, body):
        sent.update(path=path, body=body)
        return {"conferma_richiesta": True, "messaggio": "Confermi?", "comando": "systemctl restart docker"}

    monkeypatch.setattr(linux_ops, "_post", fake_post)
    out = run(linux_ops.Tools().riavvia_servizio("docker"))
    assert sent["body"] == {"service": "docker", "confirmed": False}
    assert "Confermi?" in out


def test_log_rejects_service_outside_allowlist_and_clamps_lines(monkeypatch):
    tools = linux_ops.Tools()
    assert "non consentito" in run(tools.leggi_log("sshd"))
    seen = []
    monkeypatch.setattr(linux_ops, "_get", lambda p: seen.append(p) or {"lines": 100, "log": "x"})
    run(tools.leggi_log("docker", righe=5000))
    assert seen[0].endswith("&n=100")
