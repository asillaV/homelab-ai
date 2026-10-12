#!/usr/bin/env python3
"""
paperless-organize — classifica in automatico i documenti Paperless-ngx usando Claude (CLI).

Strategia "token-aware": si processano solo i documenti non ancora classificati
(tag Inbox / senza marcatore ai-processed), con cap per run; al modello si manda un
prompt compatto = vocabolario controllato + suggerimenti del classificatore interno
di Paperless (/suggestions/) + OCR troncato. Il modello risponde SOLO con JSON.

Pensato per girare da cron con un utente dedicato. Niente dipendenze esterne:
solo stdlib (urllib) + una CLI AI (default `claude`, vedi AI_BIN / AI_MODEL).

Uso:
  paperless_organize.py --init                 crea/aggiorna vocabolario + tag tecnici
  paperless_organize.py --dry-run [--limit N]  mostra il JSON proposto, NON scrive
  paperless_organize.py [--limit N]            classifica e scrive (PATCH) i documenti
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("PAPERLESS_API_URL", "http://127.0.0.1:8000/api").rstrip("/")
TOKEN = os.environ.get("PAPERLESS_TOKEN", "")
AI_BIN = os.environ.get("AI_BIN", "claude")
AI_MODEL = os.environ.get("AI_MODEL", "sonnet")
AI_WORKDIR = os.environ.get("AI_WORKDIR", os.getcwd())
AI_TIMEOUT = 180
OCR_MAXCHARS = 1500
DEFAULT_LIMIT = 20
INBOX_TAG = "Inbox"
DONE_TAG = "ai-processed"

# Vocabolario controllato (documenti di casa). Mittenti = liberi (creati al volo).
DOC_TYPES = [
    "Fattura", "Busta paga", "Bolletta", "Ricevuta", "Contratto", "Estratto conto",
    "Referto medico", "Documento fiscale", "Comunicazione", "Garanzia", "Certificato",
]
AREA_TAGS = [
    "Casa", "Auto", "Salute", "Banca", "Lavoro", "Tasse", "Telefonia",
    "Assicurazioni", "Acquisti", "Istruzione", "Documenti personali", "Da pagare",
]


# ----------------------------------------------------------------------------- HTTP
def _req(method: str, path: str, data: dict | None = None, params: dict | None = None) -> dict:
    """Chiamata JSON all'API Paperless; su errore HTTP logga su stderr e rilancia."""
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Token " + TOKEN)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP {e.code} su {method} {path}: {e.read().decode()[:300]}\n")
        raise


def get(path: str, params: dict | None = None) -> dict:
    return _req("GET", path, params=params)


def get_all(path: str, params: dict | None = None) -> list[dict]:
    """Segue la paginazione di Paperless restituendo tutti i 'results'."""
    params = dict(params or {})
    params.setdefault("page_size", 200)
    out, nxt = [], path + "?" + urllib.parse.urlencode(params)
    while nxt:
        url = nxt if nxt.startswith("http") else API + nxt
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", "Token " + TOKEN)
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
        out.extend(d.get("results", []))
        nxt = d.get("next")
    return out


# ------------------------------------------------------------------- vocabolario
def name_map(path: str) -> dict[str, int]:
    """Mappa nome -> id per tag / tipi / mittenti."""
    return {x["name"]: x["id"] for x in get_all(path)}


def ensure(path: str, name: str, extra: dict | None = None) -> int:
    """Ritorna l'id dell'oggetto con 'name', creandolo se manca (idempotente)."""
    existing = name_map(path)
    if name in existing:
        return existing[name]
    payload = {"name": name}
    if extra:
        payload.update(extra)
    return _req("POST", path, data=payload)["id"]


def ensure_correspondent(name: str) -> int:
    """Come ensure() ma per i mittenti: match CASE-INSENSITIVE (normalizza spazi)
    per evitare duplicati tipo 'ACME ITALIA S.R.L.' vs 'Acme Italia S.r.l.'."""
    name = " ".join(name.split())
    for c in get_all("/correspondents/"):
        if " ".join(c["name"].split()).lower() == name.lower():
            return c["id"]
    return _req("POST", "/correspondents/", data={"name": name, "matching_algorithm": 0})["id"]


def cmd_init() -> None:
    """Crea (se mancano) tipi documento, tag area e tag tecnici."""
    print("Creo/aggiorno vocabolario controllato...")
    for t in DOC_TYPES:
        ensure("/document_types/", t, {"matching_algorithm": 0})
    for t in AREA_TAGS:
        ensure("/tags/", t, {"matching_algorithm": 0})
    ensure("/tags/", INBOX_TAG, {"is_inbox_tag": True, "matching_algorithm": 0})
    ensure("/tags/", DONE_TAG, {"matching_algorithm": 0})
    print(f"  tipi documento: {len(DOC_TYPES)} | tag area: {len(AREA_TAGS)} + Inbox + {DONE_TAG}")
    print("Fatto. Verifica nella UI di Paperless.")


# --------------------------------------------------------------------------- AI
def strip_json(text: str) -> str:
    """Rimuove eventuali fence ```json ... ``` e isola il primo oggetto JSON."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    i, j = t.find("{"), t.rfind("}")
    return t[i:j + 1] if i != -1 and j != -1 else t


def ask_ai(prompt: str) -> str:
    """Invoca la CLI AI in modalita' non interattiva e ritorna stdout."""
    p = subprocess.run([AI_BIN, "-p", prompt, "--model", AI_MODEL],
                       capture_output=True, text=True, timeout=AI_TIMEOUT,
                       cwd=AI_WORKDIR)
    if p.returncode != 0:
        raise RuntimeError(f"{AI_BIN} rc={p.returncode}: {p.stderr[:200]}")
    return p.stdout


def build_prompt(template: str, doc: dict, suggestions: dict) -> str:
    """Prompt compatto: template + vocabolario + suggerimenti + OCR troncato."""
    ocr = (doc.get("content") or "")[:OCR_MAXCHARS]
    sug = {k: v for k, v in suggestions.items() if v}  # solo campi non vuoti
    return (
        f"{template}\n\n"
        f"=== VOCABOLARIO ===\n"
        f"document_type (scegli UNO o null): {', '.join(DOC_TYPES)}\n"
        f"tags (scegli da 1 a 3): {', '.join(AREA_TAGS)}\n"
        f"correspondent: testo libero (il mittente/azienda) o null\n\n"
        f"=== SUGGERIMENTI PAPERLESS (puoi confermarli o correggerli) ===\n"
        f"{json.dumps(sug, ensure_ascii=False) if sug else '(nessuno)'}\n\n"
        f"=== TITOLO ATTUALE ===\n{doc.get('title','')}\n\n"
        f"=== TESTO OCR (troncato) ===\n{ocr}\n"
    )


# ------------------------------------------------------------------- pipeline
def work_queue(done_id: int, limit: int) -> list[dict]:
    """Documenti da processare: tutti quelli senza il marcatore ai-processed."""
    docs = []
    for d in get_all("/documents/", {"ordering": "-added"}):
        if done_id in d.get("tags", []):
            continue
        docs.append(d)
        if len(docs) >= limit:
            break
    return docs


def classify(args: argparse.Namespace) -> tuple[int, int, int]:
    """Classifica la coda; ritorna (processati, errori, in_coda)."""
    tmpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt.txt")
    template = open(tmpl_path, encoding="utf-8").read().strip()

    tags = name_map("/tags/")
    types = name_map("/document_types/")
    inbox_id = tags.get(INBOX_TAG)
    done_id = tags.get(DONE_TAG)
    if done_id is None or inbox_id is None:
        sys.exit("Vocabolario mancante: esegui prima --init")

    queue = work_queue(done_id, args.limit)
    print(f"Documenti in coda: {len(queue)} (limit {args.limit}, dry_run={args.dry_run})\n")

    done = errors = 0
    for d in queue:
        did = d["id"]
        try:
            sug_raw = get(f"/documents/{did}/suggestions/")
        except Exception:
            sug_raw = {}
        prompt = build_prompt(template, d, sug_raw)
        try:
            out = ask_ai(prompt)
            parsed = json.loads(strip_json(out))
        except Exception as e:
            print(f"[{did}] PARSE/AI ERRORE: {e}  (salto)")
            errors += 1
            continue

        title = (parsed.get("title") or "").strip() or None
        corr = (parsed.get("correspondent") or "").strip() or None
        dtype = (parsed.get("document_type") or "").strip() or None
        ptags = [t for t in (parsed.get("tags") or []) if t in tags]  # solo vocab
        date = (parsed.get("date") or "").strip() or None

        print(f"[{did}] «{d.get('title','')[:40]}»")
        print(f"      title={title!r} type={dtype!r} corr={corr!r} tags={ptags} date={date!r}")
        if dtype and dtype not in types:
            print(f"      ! document_type '{dtype}' fuori vocabolario -> ignorato")
            dtype = None
        if args.dry_run:
            continue

        # --- scrittura ---
        new_tags = [t for t in d.get("tags", []) if t not in (inbox_id,)]
        for tn in ptags:
            if tags[tn] not in new_tags:
                new_tags.append(tags[tn])
        if done_id not in new_tags:
            new_tags.append(done_id)

        patch = {"tags": new_tags}
        if title:
            patch["title"] = title
        if dtype:
            patch["document_type"] = types[dtype]
        if corr:
            patch["correspondent"] = ensure_correspondent(corr)
        if date and len(date) == 10:
            patch["created"] = date + "T00:00:00+00:00"
        _req("PATCH", f"/documents/{did}/", data=patch)
        done += 1

    print(f"\nRiepilogo: processati={done} errori={errors} in_coda={len(queue)}"
          + (" (dry-run, nessuna scrittura)" if args.dry_run else ""))
    return done, errors, len(queue)


def main() -> None:
    if not TOKEN:
        sys.exit("PAPERLESS_TOKEN non impostato (vedi .env.example).")
    ap = argparse.ArgumentParser(description="Auto-classifica documenti Paperless con Claude")
    ap.add_argument("--init", action="store_true", help="crea vocabolario + tag tecnici")
    ap.add_argument("--dry-run", action="store_true", help="mostra le proposte, non scrive")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="max documenti per run")
    args = ap.parse_args()
    if args.init:
        cmd_init()
        return
    classify(args)


if __name__ == "__main__":
    main()
