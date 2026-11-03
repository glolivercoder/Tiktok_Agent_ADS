# Análise da pasta `echotik-hub` — Referência para aprimorar a aplicação

**Projeto:** Tiktok_Agent_ADS  
**Data:** 03/11/2026  
**Referência:** `F:\Projetos2026\Tiktok_Agent_ADS\echotik-hub\`

---

## 1. O que a pasta `echotik-hub` contém

São **3 arquivos** que formam o frontend de referência (EchoHub) do projeto:

| Arquivo | Tamanho | Função |
|---------|---------|--------|
| `index.html` | ~2.806 linhas | Frontend único (HTML + CSS + JS) com 9 telas, design "data-terminal", navegação hash-route, gráficos SVG, agente por regras, integração com sidecar Python |
| `charts.js` | ~617 lines | Biblioteca de gráficos dependency-free (linha, barras, divergente, anel, barList, plano, bullet, timeline, matrix, timeStrip) |
| `PROMPT_MESTRE_v3_ECHOTIK.md` | ~281 lines | Especificação de produto v3: análise do prompt original, fatos verificados da EchoTok, prompt mestre pronto, arquitetura multi-agente, roadmap 4 semanas, custos, 15 ideias de viabilidade, prompts para Google Stitch/AI Studio, checklist de dados reais |

---

## 2. Arquitetura atual da aplicação principal

O projeto principal (`F:\Projetos2026\Tiktok_Agent_ADS\`) é um **sidecar Python (FastAPI)** que roda na porta `5077`:

- **Backend:** `src/echotik_agent/` — FastAPI com SQLite, LanceDB (RAG vetorial), cliente EchoTok Data API, cliente LLM (OmniRoute/OpenRouter/Gemini/Claude/DeepSeek/OpenAI), Hermes (research/market), Wechaty (mensageria), rate-limit, auditoria, skills router (80/20)
- **Endpoints expostos:** `/health`, `/chat`, `/session/sync`, `/echotok/status`, `/echotok/import`, `/diagnose`, `/decisions`, `/skills`, `/rag/*`, `/messaging/*`, etc.
- **Frontend:** NÃO é servido pelo backend (o `Dockerfile` não copia `echotik-hub/`; o `compose.yaml` não expõe porta web). O `index.html` do `echotik-hub` é um arquivo autônomo que se conecta ao sidecar via `fetch()` com token Bearer.

---

## 3. Principais atualizações do `echotik-hub` (o que é novo/avançado)

Abaixo, o que o `echotik-hub` traz de relevante para aprimorar a aplicação:

### A. Frontend completo com 9 telas (data-terminal)

1. **Visão geral** — KPIs (GMV, unidades, comissão média, verba em ads, ROAS), termômetro de temperatura (0–100°C), top 10 produtos por temperatura, sazonalidade do líder com projeção, velocidade diária 14 dias, fila de campanhas, subnichos em aquecimento.
2. **Produtos** — Tabela filtrável por categoria, ordenável por GMV/temperatura/comissão/crescimento, importação CSV.
3. **Detalhe do produto** — 3 abas: Desempenho (curva sazonal, vendas diárias), Qualidade e confiança (anel de satisfação, reclamações por tema, confiabilidade do vendedor), Ângulos de venda (ganchos e roteiro de 15s).
4. **Nichos** — Score de oportunidade (demanda − concorrência), série de crescimento 12 meses, subnichos em aquecimento.
5. **Campanhas** — KPIs da semana, painel "Proteções acionadas" (regra dos 3 dias abaixo do ROAS-alvo), tabela completa com pausar/retomar/diagnóstico.
6. **Playbook de Ads** — Dropdown de 7 plataformas (TikTok Ads, Mercado Livre, Amazon Ads, Shopee, Temu, Meta Ads, Google Ads) com passo a passo, campos-chave, gatilhos de otimização e compliance.
7. **Plano 3K** — Checklist de 8 passos de aprovação (persistido no navegador), pareto 80/20, modelo de bio, configurações do TikTok, trilha de 30 dias.
8. **Agente** — Chat por regras sobre dados da sessão, perguntas rápidas, contexto do produto selecionado, modo demonstração declarado.
9. **Dados e APIs** — Seleção de fonte (simulado / EchoTok API / CSV), tabela de planos EchoTok, calculadora de custo da API, importação de CSV com modelo para download, reset para dados simulados, zona de risco.
10. **Configurações** — Chaves e modelos de 8 provedores (OpenRouter, Gemini, OmniRouter, Claude, DeepSeek, OpenAI, SerpAPI, EchoTok), dropdowns por tipo (chat/código/imagem/TTS/embeddings), filtro grátis/pagos, teste de conexão real, travamento anti-estouro de cota, calculadora de gasto LLM por dia/30 dias.

### B. Design system "data-terminal"

- Cores: fundo `#0A0E14`, superfície `#0b0b0c`, texto `#E7ECF3`, ciano `#25F4EE` (dado/positivo), vermelho `#FE2C55` (negativo/ação primária)
- Fontes: JetBrains Mono (números/figuras), Public Sans (texto)
- Uma única `<h1>` por rota (acessibilidade AA), tabelas com `data-label` no mobile, gráficos canvas/SVG com fallback textual
- Favicon muda por rota (engrenagem em Configurações, onda nas demais)
- Responsivo: rail de ícones no desktop, phonebar + sheet no mobile

### C. Honestidade de dados (regra de produto)

- Cada tela declara a fonte: `"fonte: feed simulado"` até a API entrar
- Totais sempre calculados das linhas (nunca hardcoded)
- Botão "Voltar aos dados simulados" e teste honesto de chave (nunca finge sucesso)
- Botão de teste trava se o contador local atingir o limite free do provedor

### D. Integração com o sidecar (já implementada no index.html)

- `POST /chat` com `session`, `useRag`, `useLlm`, `limit`
- `POST /echotok/import` e `GET /echotok/status` (via sidecar, chave EchoTok nunca sai do servidor)
- `GET /health` para teste de conexão do sidecar
- Configuração de URL e token do sidecar em Configurações → Agente sidecar

---

## 4. Diferenças críticas: echotik-hub vs aplicação principal

### 4.1 O frontend não é servido pelo backend

O `Dockerfile` copia apenas `src/` e `data/skills`. O `index.html` do `echotik-hub` não está incluído na imagem Docker nem servido pelo FastAPI. Hoje ele é um arquivo solto que o usuário abre diretamente no navegador e configura a URL do sidecar manualmente.

**Impacto:** Não há deploy unificado; o usuário precisa hospedar o HTML separadamente.

### 4.2 O backend tem recursos que o frontend ainda não explora

| Recurso backend | Status no frontend (echotik-hub) |
|-----------------|----------------------------------|
| `/diagnose` (diagnóstico estruturado com sugestões de ação) | O frontend chama diagnóstico inline via regras locais, não usa o endpoint `/diagnose` |
| `/decisions` (registro de decisões com aprovação HITL) | Não utilizado pelo frontend |
| `/skills` (catálogo público de skills) | Não exposto no frontend |
| `/rag/query` e `/rag/ingest` (RAG semântico) | O frontend envia `useRag: true` mas não exibe hits RAG na UI |
| Hermes (`/hermes/research`, `/hermes/market/*`) | Não integrado no frontend |
| Wechaty/messaging (lotes, templates, DLQ) | Não integrado no frontend |
| Audit log (`/audit`) | Não exposto no frontend |

### 4.3 O frontend tem recursos que o backend não expõe

| Recurso frontend | Gap no backend |
|------------------|----------------|
| Calculadora de custo da API EchoTok (chamadas/dia × preço/mil) | Não há endpoint para calcular custo estimado |
| Calculadora de gasto LLM (tokens/dia × preço/1M × câmbio) | Não há endpoint de estimativa de custo LLM |
| Contadores de uso por provedor com travamento de cota | O backend tem rate-limit por IP, mas não por provedor/chave do usuário |
| Teste de conexão real de chave (OpenRouter, SerpAPI, etc.) | O backend não expõe teste de chave individual |

---

## 5. Recomendações de aprimoramento (priorizadas)

### Prioridade 1 — Servir o frontend pelo backend (deploy unificado)

1. Copiar `echotik-hub/index.html` e `echotik-hub/charts.js` para o projeto principal (ex.: `src/echotik_agent/static/` ou `www/`).
2. Adicionar rota no FastAPI para servir o `index.html` (e arquivos estáticos) — ex.: `GET /` retorna o hub, `GET /charts.js` retorna o script.
3. Atualizar o `Dockerfile` para copiar `echotik-hub/` (ou `static/`) na imagem.
4. Ajustar `compose.yaml` se necessário (o frontend pode ser servido na mesma porta 5077 ou em porta separada com proxy).

### Prioridade 2 — Conectar endpoints backend que o frontend ignora

1. **Usar `/diagnose`** em vez de diagnóstico apenas local: quando o usuário clica em "Diagnosticar" em uma campanha, chamar `POST /diagnose` e exibir as sugestões estruturadas (ação, razão, confiança).
2. **Exibir `/skills`**: no painel Configurações ou Agente, listar as skills disponíveis (id, nome, quando usar, intents).
3. **Exibir hits RAG**: quando o agente responde com RAG, mostrar os documentos recuperados como contexto (não como instrução), conforme a regra de honestidade.
4. **Registrar decisões (`/decisions`)**: quando o usuário pausa/retoma/escalona uma campanha, gravar a decisão no SQLite com métricas e outcome, criando trilha de auditoria.

### Prioridade 3 — Aplicar as 15 ideias de viabilidade do PROMPT_MESTRE_v3

Do documento, as mais impactantes e fáceis de implementar:

1. **Score de oportunidade transparente** — Mostrar a fórmula (crescimento × demanda × concorrência × confiança) na tela Nichos e permitir reponderar.
2. **Alerta de saturação de nicho** — Avisar quando subnicho cresce mais em criadores do que em demanda.
3. **Calendário de sazonalidade BR** — Pré-carregar Black Friday, Natal, Dia das Mães, Consciência Negra com janela de preparação (o app já projeta 30 dias; pode estender para feriados BR).
4. **Biblioteca de ganchos por temperatura** — Ganchos validados só aparecem para produtos acima de 70°C.
5. **Guardrail de ACOS para orgânico** — Meta de comissão por 1.000 views; se cair 3 dias, o agente sugere trocar o gancho.
6. **Regra de margem antes de Ads** — Só liberar verba se margem ≥ 30% e comissão média > 3× CPA estimado.
7. **HITL obrigatório para verba** — No futuro orquestrador LangGraph, `interrupt_before` em nós de mudança de verba.
8. **Cache de API com TTL 24h** — Rankings mudam devagar; cache reduz 80% das chamadas da EchoTok.
9. **Log de decisões do agente** — Cada pausa/reforço proposta fica registrado com números da semana.
10. **Exportação de prova** — Botão que gera resumo semanal (GMV, ROAS, decisões) em texto para colar no diário.
11. **Modo "dupla-check" de compliance** — 4ª regra sempre no playbook: revisar docs oficiais antes de publicar.
12. **Kits por subnicho** — Agrupar produto + 3 ganchos + 1 roteiro + horários sugeridos como "kit" de 1 dia de postagem.
13. **Circuito semanal de 30 min** — Segunda-feira = importar CSV, escolher 2 produtos, agendar 7 roteiros.

### Prioridade 4 — Melhorias de UX/design do frontend (já estão no echotik-hub, garantir que estejam na versão servida)

- Manter o design data-terminal (cores, fontes, uma h1 por rota)
- Manter a honestidade de dados (badge "fonte: feed simulado" em toda tela)
- Manter o travamento de cota free por provedor
- Manter o favicon por rota e a navegação responsiva (rail + phonebar + sheet)

### Prioridade 5 — Backend: expor endpoints de cálculo de custo

1. `GET /custo/echotok?chamadas=300&preco=5` → retorna custo mensal estimado (usado pela calculadora da aba Dados).
2. `GET /custo/llm?provedor=gemini&modelo=...&tokensIn=120000&tokensOut=40000&fx=5.40` → custo por dia e 30 dias.
3. `GET /uso/provedores` → retorna contadores de uso local por provedor (dia/mês) para o frontend exibir e travar.

---

## 6. Plano de ação sugerido

| Semana | Ação | Arquivos envolvidos |
|--------|------|---------------------|
| 1 | Servir `index.html` + `charts.js` pelo FastAPI; atualizar Dockerfile/compose | `Dockerfile`, `compose.yaml`, `src/echotik_agent/main.py` (nova rota estática), copiar `echotik-hub/*` |
| 1 | Conectar `/diagnose` ao botão Diagnosticar das campanhas | `src/echotik_agent/main.py` (já existe), `echotik-hub/index.html` (chamar endpoint) |
| 2 | Exibir catálogo `/skills` e hits RAG na UI do agente | `index.html`, `main.py` |
| 2 | Registrar decisões (`/decisions`) ao pausar/retomar campanhas | `index.html`, `main.py` (já existe) |
| 3 | Endpoints de custo (`/custo/echotok`, `/custo/llm`, `/uso/provedores`) | `main.py`, `index.html` (calculadoras) |
| 3 | Aplicar ideias de viabilidade: score transparente, alerta de saturação, calendário sazonal BR | `index.html` (dados e telas) |
| 4 | Exportação de prova semanal, kits por subnicho, circuito semanal | `index.html` |

---

## 7. Conclusão

A pasta `echotik-hub` é a **referência de frontend completa e madura** do projeto EchoTok Hub. Ela já está alinhada com o backend (usa `/chat`, `/echotok/import`, `/echotok/status`, `/health`) e incorpora as melhores práticas do PROMPT_MESTRE_v3 (honestidade de dados, guardrail de Ads, Plano 3K, design data-terminal, 8 provedores de LLM/dados, calculadoras de custo).

O principal gap hoje é **operacional/deploy**: o frontend não é servido pelo backend e vários endpoints úteis do backend (`/diagnose`, `/decisions`, `/skills`, RAG, Hermes) ainda não são explorados pela UI. Aplicar as recomendações acima unifica o deploy e aproveita o potencial total da arquitetura multi-agente já definida no PROMPT_MESTRE_v3.
