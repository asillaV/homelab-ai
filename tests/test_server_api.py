"""Offline tests for server_api (no real shell commands, no NetAlertX)."""
import server_api


def test_services_use_only_safe_names_and_are_quoted(monkeypatch):
    cmds = []
    monkeypatch.setattr(server_api, "run_cmd", lambda c, timeout=10: cmds.append(c) or "active")
    monkeypatch.setattr(server_api, "MONITORED_SERVICES", ["docker"])
    out = server_api.get_services(compact=True)
    assert out == {"down": "tutti attivi"}
    assert cmds == ["systemctl is-active docker 2>/dev/null"]


def test_devices_returns_error_when_netalertx_unreachable(monkeypatch):
    def boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(server_api.urllib.request, "urlopen", boom)
    assert "error" in server_api.get_devices()


def test_presence_not_configured(monkeypatch):
    monkeypatch.setattr(server_api, "PRESENCE_CMD", "")
    assert "non configurato" in server_api.get_presence()["presence"]
