# Tiktok_Agent_ADS

Sidecar Python (FastAPI) para o EchoTok Hub: SQLite (campanhas/decisões) + RAG vetorial (LanceDB) + regras de diagnóstico de campanhas + chat com LLM opcional e roteador de skills. Segredos nunca entram no RAG — vêm do `.env` local (gerado por `scripts/import_keys.ps1`).

## Rodar

```powershell
cd Tiktok_Agent_ADS
uv sync --frozen
.\scripts\import_keys.ps1   # cria .env local (mascarado)
uv run --frozen uvicorn echotik_agent.main:create_app --factory --host 127.0.0.1 --port 5077
```

Health: `GET http://127.0.0.1:5077/health` (aberto por padrão; desative com `ECHOTIK_OPEN_HEALTH=false`).

## LLM e skills

- `/chat` tenta **OmniRoute** (`OMNIROUTE_*`) e, se falhar, **OpenRouter** (`OPENROUTER_*`).
- Sem provedor disponível, cai nas regras locais (`mode="rules"`).
- `data/skills/strategic_skills.json` define as 5 skills 80/20; o roteador carrega no máximo 3 por mensagem e respeita orçamento de ~1.500 tokens.
- `GET /skills` lista o catálogo (sem prompts completos); `POST /chat` devolve `skillsUsed`, `llmProvider` e `llmModel`.
- `POST /chat` sem `session` usa a última sessão salva em `POST /session/sync`.

## Docker

```powershell
docker compose up --build
```

O container escuta apenas em `127.0.0.1:5077`, roda como usuário não-root, monta `./data` em `/data` e lê o `.env` local (não versionado). O `compose.yaml` usa `read_only`, `tmpfs` e `no-new-privileges`. A imagem define `ECHOTIK_SKILLS_PATH` para o catálogo copiado em `/app/data/skills`.

## Rate-limit e auditoria

- `ECHOTIK_RATE_LIMIT_PER_MIN` (default 120) e `ECHOTIK_RATE_LIMIT_BURST` (default 30) aplicam um token bucket por IP. `0` desativa.
- `/health` e preflight `OPTIONS` ficam fora do limite.
- A tabela `audit_log` registra evento, IP e detalhe sem conteúdo bruto: em `/chat` guarda só tamanho + SHA-256 truncado da mensagem, modo, skills, provedor e número de campanhas.
- `GET /audit?limit=50` (Bearer) lista as últimas ações.

## Testes

```powershell
uv run --frozen pytest
```

## Integração com Hermes

- `HERMES_SECRETS_URL` (default 5011), `HERMES_RESEARCH_URL` (5021), `HERMES_MARKET_URL` (5022), `HERMES_HTTP_TOKEN`.
- `AGENCY_AGENTS_DIR` aponta para o clone de `Agency_agents` para seed de personas (`POST /seed/personas`).

## EchoTok Data API (via sidecar)

- `ECHOTOK_API_USER` + `ECHOTOK_API_PASSWORD` (Basic auth) **ou** `ECHOTOK_API_KEY` (única, vira `user:pass` ou `chave:`).
- Base: `ECHOTIK_API_BASE_URL` (default `https://open.echotik.live/api/v3`), região `ECHOTIK_REGION=BR`.
- Cota diária: `ECHOTIK_API_DAILY_LIMIT=50` (contador em `api_usage` SQLite).
- Endpoints:
  - `GET /echotok/status` → `{configured, baseUrl, region, dailyLimit, usedToday, remaining}`
  - `POST /echotok/import` → importa produtos normalizados (campos `EchoTokProduct`); body: `{region, pageSize, pageNum, categoryId, sortField, sortType, language, includeCategories}`
  - Hub chama via token do sidecar; a chave EchoTok **nunca** sai do servidor.

## Hermes Research / Market (via sidecar)

- `HERMES_RESEARCH_URL`, `HERMES_MARKET_URL`, `HERMES_HTTP_TOKEN`, `HERMES_HTTP_TIMEOUT`.
- Endpoints (Bearer token do sidecar):
  - `GET /hermes/health` → status research/market
  - `GET /hermes/research?q=...&limit=10&region=BR&language=pt-BR`
  - `GET /hermes/research/skills?intent=campaign-decision&limit=5`
  - `GET /hermes/market/product?product_id=...&region=BR`
  - `GET /hermes/market/competitors?category_id=...&limit=10&region=BR`
  - `GET /hermes/market/trends?category_id=...&subcategory_id=...&days=30&region=BR`

## Embeddings semânticos (opcional)

- Provider: `ECHOTIK_EMBED_PROVIDER=gemini|openai|hash` (auto-detecta por chaves).
- Modelo: `ECHOTIK_EMBED_MODEL` (default `text-embedding-004` p/ Gemini, `text-embedding-3-small` p/ OpenAI).
- Dimensão: `ECHOTIK_EMBED_DIM=384` (compatível LanceDB existente; Gemini `outputDimensionality`, OpenAI `dimensions`).
- Fallback: se a chamada remota falhar, usa hash local sem derrubar o sidecar.
- `seed_skills` no boot ingere `strategic_skills.json` + markdowns de compliance/SEO na collection `skills`.

## Segurança

- `.env` fica fora do Git (`.gitignore`); o repositório só versiona `.env.example` sem valores.
- O script de importação imprime apenas provedor + tamanho + 4 últimos caracteres.
- Chaves nunca entram no SQLite, no LanceDB/RAG nem nas respostas da API.
- Se uma chave apareceu em log, histórico de Git ou nome de arquivo, **rotacione** no painel do provedor e rode o script de importação de novo.

## Isolamento

Projeto independente; pode ser versionado em repositório próprio (https://github.com/glolivercoder/Tiktok_Agent_ADS).
