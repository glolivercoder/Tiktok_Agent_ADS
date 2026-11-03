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

## Testes

```powershell
uv run --frozen pytest
```

## Integração com Hermes

- `HERMES_SECRETS_URL` (default 5011), `HERMES_RESEARCH_URL` (5021), `HERMES_MARKET_URL` (5022), `HERMES_HTTP_TOKEN`.
- `AGENCY_AGENTS_DIR` aponta para o clone de `Agency_agents` para seed de personas (`POST /seed/personas`).

## Isolamento

Projeto independente; pode ser versionado em repositório próprio (https://github.com/glolivercoder/Tiktok_Agent_ADS).
