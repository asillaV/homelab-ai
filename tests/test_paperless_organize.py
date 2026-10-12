"""Offline tests for paperless_organize: no network, no real Claude CLI."""
import argparse
import io
import json
import urllib.error

import paperless_organize as po
import pytest


# ------------------------------------------------------------ JSON parsing
@pytest.mark.parametrize(
    "raw",
    [
        '{"title": "Bolletta luce", "tags": ["Casa"]}',
        '```json\n{"title": "Bolletta luce", "tags": ["Casa"]}\n```',
        'Ecco il risultato:\n{"title": "Bolletta luce", "tags": ["Casa"]}\nFine.',
    ],
)
def test_strip_json_valid_variants(raw):
    parsed = json.loads(po.strip_json(raw))
    assert parsed["title"] == "Bolletta luce"
    assert parsed["tags"] == ["Casa"]


@pytest.mark.parametrize("raw", ["", "nessun json qui", '{"title": "tronco', "{title: senza apici}"])
def test_strip_json_malformed_is_not_parseable(raw):
    with pytest.raises(json.JSONDecodeError):
        json.loads(po.strip_json(raw))


# ------------------------------------------------------------ prompt
def test_build_prompt_truncates_ocr_to_limit():
    doc = {"title": "Doc", "content": "A" * 5000 + "FINE"}
    prompt = po.build_prompt("TEMPLATE", doc, {})
    assert "A" * po.OCR_MAXCHARS in prompt
    assert "A" * (po.OCR_MAXCHARS + 1) not in prompt
    assert "FINE" not in prompt


def test_build_prompt_includes_vocabulary_and_drops_empty_suggestions():
    prompt = po.build_prompt("T", {"title": "x", "content": "y"}, {"tags": [3], "correspondent": None})
    assert "Fattura" in prompt and "Casa" in prompt
    assert '"tags": [3]' in prompt
    assert "correspondent" not in prompt.split("SUGGERIMENTI")[1].split("TITOLO")[0]


def test_build_prompt_without_suggestions():
    assert "(nessuno)" in po.build_prompt("T", {"title": "x"}, {})


# ------------------------------------------------------------ pipeline
class FakeApi:
    """Replaces the Paperless HTTP layer and the AI call."""

    def __init__(self, docs, ai_output):
        self.docs = docs
        self.ai_output = ai_output
        self.patches = []
        self.ai_calls = 0

    def install(self, monkeypatch):
        tags = {"Inbox": 1, "ai-processed": 2, "Casa": 3}
        monkeypatch.setattr(po, "name_map", lambda path: tags if path == "/tags/" else {"Bolletta": 10})
        monkeypatch.setattr(po, "get_all", lambda path, params=None: list(self.docs))
        monkeypatch.setattr(po, "get", lambda path, params=None: {})
        monkeypatch.setattr(po, "ask_ai", self._ask)
        monkeypatch.setattr(po, "ensure_correspondent", lambda name: 99)
        monkeypatch.setattr(po, "_req", self._req)

    def _ask(self, prompt):
        self.ai_calls += 1
        return self.ai_output

    def _req(self, method, path, data=None, params=None):
        self.patches.append((method, path, data))
        return {}


ARGS = argparse.Namespace(limit=20, dry_run=False)
AI_OK = '{"title": "Bolletta luce", "correspondent": "ACME", "document_type": "Bolletta", "tags": ["Casa"]}'


def test_already_processed_document_is_skipped(monkeypatch):
    api = FakeApi([{"id": 1, "title": "old", "tags": [2], "content": "x"}], AI_OK)
    api.install(monkeypatch)
    done, errors, queued = po.classify(ARGS)
    assert (done, errors, queued) == (0, 0, 0)
    assert api.ai_calls == 0 and api.patches == []


def test_new_document_is_patched_and_marked_processed(monkeypatch):
    api = FakeApi([{"id": 7, "title": "scan", "tags": [1], "content": "x"}], AI_OK)
    api.install(monkeypatch)
    assert po.classify(ARGS) == (1, 0, 1)
    method, path, data = api.patches[0]
    assert (method, path) == ("PATCH", "/documents/7/")
    assert data["title"] == "Bolletta luce"
    assert data["document_type"] == 10 and data["correspondent"] == 99
    assert 2 in data["tags"] and 3 in data["tags"] and 1 not in data["tags"]


def test_dry_run_does_not_write(monkeypatch):
    api = FakeApi([{"id": 7, "title": "scan", "tags": [], "content": "x"}], AI_OK)
    api.install(monkeypatch)
    po.classify(argparse.Namespace(limit=5, dry_run=True))
    assert api.patches == []


def test_malformed_ai_output_is_counted_as_error_and_skipped(monkeypatch):
    api = FakeApi([{"id": 7, "title": "scan", "tags": [], "content": "x"}], "non è json")
    api.install(monkeypatch)
    assert po.classify(ARGS) == (0, 1, 1)
    assert api.patches == []


def test_unknown_tags_are_ignored(monkeypatch):
    out = '{"title": "T", "tags": ["Inventato", "Casa"]}'
    api = FakeApi([{"id": 7, "title": "scan", "tags": [], "content": "x"}], out)
    api.install(monkeypatch)
    po.classify(ARGS)
    assert 3 in api.patches[0][2]["tags"]
    assert len(api.patches[0][2]["tags"]) == 2  # Casa + ai-processed


# ------------------------------------------------------------ HTTP errors
def test_req_logs_and_reraises_http_error(monkeypatch, capsys):
    def boom(req, timeout=None):
        body = io.BytesIO(b'{"tags":["invalid"]}')
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, body)

    monkeypatch.setattr(po.urllib.request, "urlopen", boom)
    with pytest.raises(urllib.error.HTTPError):
        po._req("PATCH", "/documents/7/", data={"tags": [999]})
    err = capsys.readouterr().err
    assert "HTTP 400" in err and "PATCH /documents/7/" in err


def test_req_sends_token_header_and_parses_json(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        seen["method"] = req.get_method()
        return Resp(b'{"ok": true}')

    monkeypatch.setattr(po, "TOKEN", "test-token")
    monkeypatch.setattr(po.urllib.request, "urlopen", fake)
    assert po._req("GET", "/tags/") == {"ok": True}
    assert seen == {"auth": "Token test-token", "method": "GET"}


def test_ask_ai_raises_on_nonzero_exit(monkeypatch):
    class P:
        returncode, stdout, stderr = 1, "", "not logged in"

    monkeypatch.setattr(po.subprocess, "run", lambda *a, **k: P())
    with pytest.raises(RuntimeError, match="rc=1"):
        po.ask_ai("hi")
