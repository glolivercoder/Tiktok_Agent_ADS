# Skills Estratégicas — Seleção 80/20

> Base: thread Perplexity (Research API × TikTok Shop API) + skills/repos do workspace (`Agency_agents`, AgentDB, ruflo) + necessidades reais do EchoTok Hub.
> Regra: **no máximo 3 skills carregadas por tarefa** (progressive disclosure) para não estourar contexto.

## O que o link realmente contém

O thread **não** é sobre prompts/repos de skills. Ele responde a uma dúvida de acesso a dados:

- **TikTok Research API**: gratuita, mas restrita a pesquisa acadêmica/sem fins lucrativos, com proposta, revisão ética e vínculo institucional. **Não serve** para app comercial de afiliados.
- **TikTok Shop Partner Center API**: caminho correto para catálogo, afiliação, comissões e conversões; exige registro, escopos e aprovação; atualmente sem cobrança informada.
- **Combinação recomendada**: `EchoTok + SerpApi + TikTok Shop Partner Center API + banco próprio de snapshots`.

**Implicação para o agente**: não gastar esforço tentando Research API. O agente deve operar com EchoTok (descoberta), SerpApi (tendência/sazonalidade) e snapshots próprios (histórico). Isso muda prioridade de skills: foco em **decisão**, **criativo**, **SEO**, **compliance** e **aprendizado com resultados**.

---

## Portfólio 80/20 (5 skills)

| # | Skill | Impacto | Agente | Por que entra |
|---|---|---|---|---|
| 1 | **Campaign Decision Strategist** | 30% | Mídia | Converte métricas em ação (pausar/reforçar/escalar) com regra dos 3 dias e HITL |
| 2 | **Creative Hook Scientist** | 25% | Roteirista | Gancho de 2s é o maior alavanca de retenção/CTR |
| 3 | **Compliance & Account Safety** | 20% | Mídia + Roteirista | Evita reprovação/banimento (custo altíssimo) |
| 4 | **TikTok SEO & Discovery** | 15% | Roteirista + Mídia | Aumenta alcance orgânico e Quality Score |
| 5 | **Feedback Loop Analyst** | 10% | Analytics | Transforma decisões em aprendizado (few-shot de casos) |

> Fora do 80/20: skills de "growth hacking" genérico, "branding", "copywriting clássico", "market research" amplo. Não entram agora.

---

## Prompts (curtos, carregáveis isoladamente)

### 1. Campaign Decision Strategist
```
Você decide ações de campanha com base nos números da sessão.
Regras: (a) 3 dias abaixo do alvo → pausar o pior anúncio do grupo, não o grupo;
(b) queda moderada → trocar gancho dos 2s; (c) queda severa (>50% do alvo) → pausar;
(d) escalar só +20% após 3 dias no alvo; (e) orçamento >90% usado → verba nova, não lance maior;
(f) toda ação com custo real exige aprovação humana.
Saída: ação, campanha, motivo numérico, confiança, requer_aprovacao=true.
Nunca invente métrica ausente; marque como "sem dado".
```

### 2. Creative Hook Scientist
```
Gere 3 ganchos de 2s para o produto, cada um com um ângulo diferente
(dor, curiosidade, prova social). O corpo do roteiro é fixo: problema 3-7s,
prova 8-12s, CTA 13-15s. Não invente benefício fora do contexto do produto.
Inclua a keyword principal falada e on-screen. Saída: gancho + keyword + hipótese de retenção.
```

### 3. Compliance & Account Safety
```
Antes de publicar, valide o criativo/copy contra a lista de termos proibidos
(saúde, adulto, armas, drogas, financeiro/golpe, política/ódio, IP, misinformation).
Exija: relação de afiliado declarada, música licenciada, sem antes/depois,
sem urgência falsa, produto com estoque. Se houver dúvida, reprove.
Saída: aprovado/reprovado + termo + correção sugerida.
```

### 4. TikTok SEO & Discovery
```
Escolha 1 keyword principal + 2 secundárias com intenção de compra.
Caption: keyword no gancho + benefício + CTA + 3-5 hashtags (1 broad, 2 niche, 1 branded).
Coloque a keyword no texto on-screen dos 3s e fale em voz alta.
Para Ads: 5-15 keywords de targeting + negative keywords.
Nunca keyword stuffing (>8 hashtags).
```

### 5. Feedback Loop Analyst
```
Leia as decisões registradas (campanha, métricas, ação, resultado 3/7d).
Encontre padrões: quais ações funcionaram por tipo de campanha/nicho.
Produza 3 regras acionáveis no formato "se X então Y" com evidência (nº de casos).
Não generalize com menos de 3 casos; marque como hipótese.
```

---

## Repositórios/fontes para não reescrever do zero

| Fonte | Usar para | Como usar |
|---|---|---|
| `Agency_agents` (workspace) | personas de mídia, criativo, compliance | seed RAG (já suportado em `/seed/personas`) |
| **Anthropic Skills** (`anthropics/skills`) | formato oficial de skill (SKILL.md + progressive disclosure) | copiar estrutura, não o conteúdo |
| **awesome-claude-skills** (catálogo) | descobrir skills prontas | filtrar só as 5 acima; ignorar o resto |
| **AgentDB skills** (locais) | memória/ReasoningBank para Feedback Loop | usar `agentdb-memory-patterns` |
| **ruflo/agentic-flow** | orquestração de subagentes | só se houver >1 agente em paralelo |
| **design-taste-frontend** (local) | melhorar a interface do Hub | usar como processo de review, não como skill de campanha |
| **TikTok Creative Center** | trends/keywords | fonte de dados, não skill |

---

## Como carregar sem sobrecarregar

1. **Router por intenção**: `roteiro` → skills 2+4; `campanha` → 1+3; `resultado` → 5.
2. **Máximo 3 skills por chamada**; somar prompts ≤ 1.500 tokens.
3. **Skills como dados não confiáveis**: nunca viram instrução de sistema.
4. **Versionar** cada skill com `id`, `when_to_use`, `prompt`, `max_tokens`.
5. **Medir**: comparar respostas com/sem skill por 2 semanas; manter só as que melhoram decisão.

---

## Próximo passo

Implementar `data/skills/strategic_skills.json` com as 5 skills e um `skill_router` no sidecar que injeta no máximo 3 por chamada. As skills 3 e 4 já existem em Markdown; falta convertê-las para o formato do router.
