# Central RAG Knowledge Base — Runbook

A step-by-step record of how we stood up the company's central RAG knowledge base
using **R2R**, including the setup, the problems we hit, and the fixes we baked in.

> **Status:** working locally. Ingest + RAG + per-user access control all verified.
> **Stack:** R2R (patched image) · PostgreSQL/pgvector · Ollama (embeddings) · Groq (LLM).

---

## 1. Why R2R (over RAGFlow)

We evaluated **RAGFlow** and **R2R** and chose R2R for the central knowledge base:

| Need | Why R2R fit |
| --- | --- |
| **Multi-tenant access control** | First-class **users + collections**; a user only retrieves from collections they belong to. Central to "one company KB, many teams." |
| **API-first** | Clean REST + Python SDK (`R2RClient`) — easy to embed in internal tools. |
| **Pluggable models** | Provider-agnostic via litellm — mix local (Ollama embeddings) and cloud (Groq LLM) freely. |
| **Self-hostable** | Ships as Docker images; runs entirely on our own infra. |

RAGFlow has a strong document-parsing UI, but R2R's user/collection permission model and
API-first design mapped better to a shared, access-controlled company KB.

---

## 2. Architecture

```
                    ┌─────────────────────────────────────────┐
   your code /      │  R2R container  (port 7272 → 8000)       │
   dashboard  ─────▶│   • REST API + SDK                        │
   (7272 / 3000)    │   • users, collections, RAG orchestration │
                    └──────┬───────────────┬───────────────┬────┘
                           │               │               │
                  embeddings│         LLM   │        metadata│ + vectors
                           ▼               ▼               ▼
                  ┌──────────────┐  ┌────────────┐  ┌──────────────────┐
                  │ Ollama (host)│  │ Groq (API) │  │ Postgres/pgvector│
                  │ mxbai-embed  │  │ llama-3.x  │  │  (container)     │
                  │ :11434       │  └────────────┘  └──────────────────┘
                  └──────────────┘
```

- **Embeddings** → local **Ollama** (`mxbai-embed-large`, 1024-dim). No per-token cost, data stays local.
- **LLM (RAG answers, summaries)** → **Groq** (`llama-3.3-70b-versatile` / `llama-3.1-8b-instant`). Fast, no local GPU needed.
- **Storage** → **Postgres + pgvector** (documents, chunks, vectors, users, collections).
- **Dashboard** → R2R web UI on port 3000 (optional).

---

## 3. Files in this project

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | Defines the 3 services (postgres, r2r, dashboard). |
| `Dockerfile.r2r` | Builds a **patched** R2R image (see §6, Issue 4). |
| `my_config.toml` | R2R model config — which models play which role. |
| `.env` | Secrets — holds `GROQ_API_KEY` (do **not** commit). |
| `test.py` | Smoke test — ingests docs, creates users/collections, runs RAG per user. |

---

## 4. Prerequisites (one-time host setup)

1. **Docker + Docker Compose** installed.
2. **Ollama** installed on the host and the embedding model pulled:
   ```bash
   ollama pull mxbai-embed-large
   ```
3. **Ollama must listen on all interfaces** so the R2R container can reach it
   (by default it binds to `127.0.0.1` only, which Docker can't reach):
   ```bash
   sudo mkdir -p /etc/systemd/system/ollama.service.d
   printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0"\n' \
     | sudo tee /etc/systemd/system/ollama.service.d/override.conf
   sudo systemctl daemon-reload && sudo systemctl restart ollama
   # verify: `ss -ltnp | grep 11434` should show *:11434 (not 127.0.0.1:11434)
   ```
4. **Groq API key** — put it in `.env`:
   ```
   GROQ_API_KEY=gsk_...
   ```
5. **Python SDK** for running the smoke test:
   ```bash
   pip install r2r
   ```

---

## 5. Bring it up

```bash
cd /home/ashraful/Programming/knowledge-base

# Build the patched R2R image and start everything
docker compose up -d --build

# Wait until healthy, then smoke-test
curl -s http://localhost:7272/v3/health      # -> {"results":{"message":"ok"}}
python3 test.py
```

Endpoints:
- **API:** http://localhost:7272
- **Dashboard:** http://localhost:3000

To restart just R2R after a config change:
```bash
docker compose restart r2r      # my_config.toml is bind-mounted, so a restart reloads it
```

---

## 6. Problems we hit and how we fixed them

These are the real issues we debugged, in the order they surfaced. Each fix is already
applied in the files above — this section is so we (and the next person) understand *why*.

### Issue 1 — Container couldn't reach Ollama (`Connection refused`)
- **Cause:** Ollama was bound to `127.0.0.1:11434`; the R2R container reaches the host via
  `host.docker.internal` (the Docker bridge), which loopback-only Ollama refuses.
- **Fix:** `OLLAMA_HOST=0.0.0.0` (see §4, step 3) so Ollama accepts connections from the bridge.

### Issue 2 — Error pointed at `localhost:11434`
- **Cause:** `my_config.toml` hardcoded `http://localhost:11434`. Inside the container,
  `localhost` is the container itself, not the host.
- **Fix:** the container reaches the host via `OLLAMA_API_BASE: http://host.docker.internal:11434`
  (set in `docker-compose.yml`), plus `extra_hosts: host.docker.internal:host-gateway`.

### Issue 3 — `404 page not found` from Ollama during ingestion
- **Cause:** config referenced `ollama/llama3.1` as the LLM, but that model was never pulled —
  only the embedder (`mxbai-embed-large`) was present.
- **Fix:** switched all LLM roles to **Groq** in `my_config.toml`
  (`quality_llm = "groq/llama-3.3-70b-versatile"`, `fast_llm = "groq/llama-3.1-8b-instant"`).
  Embeddings stay on local Ollama. No 5 GB model download needed.

### Issue 4 — `service_tier … Input should be 'scale' or 'default'` (HTTP 500) ⚠️ the tricky one
- **Cause:** a **version-skew bug inside the official `sciphiai/r2r:latest` image**. It ships
  **R2R 3.6.6 + litellm 1.75.8**. litellm stamps `service_tier="auto"` onto every completion
  response, but R2R's `LLMChatCompletion` model only allows `Literal["scale","default"]`, so R2R
  rejects its own dependency's output → 500 on **every** ingest and RAG call.
- **Not fixable via config:** litellm overwrites `service_tier` in the *response* regardless of
  what you request, so no `my_config.toml` value avoids it.
- **Fix:** a thin derived image (`Dockerfile.r2r`) that widens the field to `Optional[str]`:
  ```dockerfile
  FROM sciphiai/r2r:latest
  RUN sed -i 's/service_tier: Optional\[Literal\["scale", "default"\]\] = None/service_tier: Optional[str] = None/' \
        /app/shared/abstractions/llm.py \
      && grep -q 'service_tier: Optional\[str\] = None' /app/shared/abstractions/llm.py
  ```
  The trailing `grep` **fails the build** if upstream ever changes that line, so a silent
  regression can't slip into production.
- **Why an image, not a bind-mount:** the patch is baked into the artifact we push to a
  registry, so it deploys to any cloud node and survives `docker compose down && up`.
  (An earlier in-container `sed` was wiped the moment the container was recreated — don't do that.)

---

## 7. How multi-tenant access control works (verified in `test.py`)

R2R enforces retrieval permissions through **users** and **collections**:

1. Create documents, then create collections (e.g. `hr-docs`, `eng-docs`).
2. Add each document to the right collection.
3. Create users (e.g. `alice`, `bob`) and add them to the collections they're allowed to see.
4. When a user runs `retrieval.rag(...)`, R2R only searches the collections that user belongs to.

Result: Alice (in `hr-docs`) and Bob (in `eng-docs`) asking the *same* question get answers
grounded only in *their own* documents. This is the core property we need for a shared,
access-controlled company KB.

---

## 8. Operational notes / TODO before production

- [ ] **Pin the base image by digest** in `Dockerfile.r2r`
      (`FROM sciphiai/r2r@sha256:<digest>`) — `:latest` is not reproducible and a re-pull
      could re-break or silently change versions. Get it with:
      `docker inspect --format '{{index .RepoDigests 0}}' sciphiai/r2r:latest`
- [ ] **Change default credentials.** `test.py` logs in as `admin@example.com / change_me_immediately`
      and creates users with demo passwords — replace before any real use.
- [ ] **Secrets management.** `.env` currently holds a live `GROQ_API_KEY` in plaintext — move to
      a secrets manager for cloud, and keep `.env` out of git.
- [ ] **Postgres durability.** Data lives in the `pgdata` volume; set up backups. Consider a
      managed Postgres (with pgvector) in the cloud instead of the container.
- [ ] **Push the patched image** to the company registry so cloud nodes pull it (`docker build`,
      `docker push`), rather than rebuilding on each host.
- [ ] **Track the upstream R2R bug** — a newer R2R release may widen the `service_tier` type and
      let us drop `Dockerfile.r2r` entirely.
- [ ] **VLM / audio models** in `my_config.toml` are placeholders pointing at a text model —
      swap for real vision/transcription models if we ingest images or audio.

---

## 9. Quick command reference

```bash
# Start / rebuild everything
docker compose up -d --build

# Restart R2R after editing my_config.toml
docker compose restart r2r

# Health check
curl -s http://localhost:7272/v3/health

# Tail R2R logs
docker compose logs -f r2r

# Confirm the service_tier patch is present in the running container
docker exec $(docker ps --filter name=r2r -q) \
  grep -n 'service_tier: Optional' /app/shared/abstractions/llm.py

# Stop everything (keeps data volume)
docker compose down
```
