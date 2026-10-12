# Architecture

The repo contains three independent pieces that share one host and one design rule:
**keep the LLM at the edge and the logic deterministic and testable.**

## 1. Paperless classification job

`run.sh` (cron) loads the environment and calls `paperless_organize.py`, which:

1. lists documents newest-first and drops those that already carry the `ai-processed` tag (idempotency, no state file);
2. for each of up to `--limit` documents, builds a compact prompt: instruction template, controlled vocabulary (document types and area tags), Paperless' own `/suggestions/`, current title and the first 1500 characters of OCR text;
3. runs `claude -p <prompt> --model <model>` as a subprocess and parses the JSON answer defensively (`strip_json` removes code fences/preamble; unparsable answers are counted as errors and skipped);
4. validates the answer against the vocabulary (unknown tags/types are dropped), creates correspondents on the fly with case-insensitive de-duplication, and `PATCH`es the document, adding `ai-processed`.

The AI call is a single function (`ask_ai`), so the backend CLI/model can be swapped through `AI_BIN` / `AI_MODEL`.

## 2. Open WebUI tools + server-api

Open WebUI talks to a local OpenAI-compatible LLM server and exposes the files in `openwebui-tools/` as callable tools. Each tool is a thin formatter around a JSON endpoint:

* `server_homelab` → `server-api` (read-only: `/status`, `/containers`, `/services`, `/devices`, `/presence`);
* `linux_ops` → a separate backend for diagnostics and actions. Actions require `confirmed=true` (the model must first show the command and wait for the user) **and** a service in `LINUX_OPS_ALLOWED_SERVICES`.

Failure mode: if the backend is down, tools return a plain `Errore: ...` string instead of raising, so the model can tell the user.

## Screenshots

TODO: screenshot Open WebUI (tool call in a chat)

TODO: screenshot Paperless document list with `ai-processed` tag

TODO: screenshot CI run
