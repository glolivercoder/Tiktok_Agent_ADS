# Tiktok_Agent_ADS

Sidecar Python (FastAPI) para o EchoTok Hub: SQLite (campanhas/decisões) + RAG vetorial (LanceDB) + regras de diagnóstico de campanhas + chat por regras. Segredos nunca entram no RAG — vêm do Hermes `secrets-config` (ou env).

## Rodar

```powershell
cd Tiktok_Agent_ADS
uv sync --frozen
$env:ECHOTIK_AGENT_TOKEN = (openssl rand -hex 32)
uv run --frozen uvicorn echotik_agent.main:create_app --factory --host 127.0.0.1 --port 5077
```

Health: `GET http://127.0.0.1:5077/health` (aberto por padrão; desative com `ECHOTIK_OPEN_HEALTH=false`).

## Testes

```powershell
uv run --frozen pytest
```

## Integração com Hermes

- `HERMES_SECRETS_URL` (default 5011), `HERMES_RESEARCH_URL` (5021), `HERMES_MARKET_URL` (5022), `HERMES_HTTP_TOKEN`.
- `AGENCY_AGENTS_DIR` aponta para o clone de `Agency_agents` para seed de personas (`POST /seed/personas`).

## Isolamento

Projeto independente; pode ser versionado em repositório próprio (https://github.com/glolivercoder/Tiktok_Agent_ADS).
