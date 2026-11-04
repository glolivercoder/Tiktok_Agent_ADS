# Playbook de métricas — market-intel

Limiares marcados com (sug.) são sugestões deste projeto, não regra da EchoTik nem da Apify. Ajuste ao seu nicho e registre a mudança.

## 1. Limpeza e normalização

| Situação | Tratamento |
|---|---|
| ID vazio | descartar |
| Duplicata (fonte, id) | manter a coleta mais recente |
| Comissão entre 0 e 1 | multiplicar por 100 (a API pode devolver fração) |
| Nota acima de 5 | dividir por 10 (escala 0-50) |
| Preço ≤ 0, vazio ou mascarado (ex.: `5*`) | `price_quality=invalido`; fora das estatísticas |
| Moeda | guardar sempre junto do preço; converter só com câmbio datado |
| Outlier de preço | fora de [Q1−1,5·IQR; Q3+1,5·IQR]: marcar, não apagar; reportar com e sem |
| Região ou categoria diferentes | não comparar; usar a categoria mais específica disponível (`category_l3`) |

Registre sempre: linhas lidas, descartadas, motivo.

## 2. Demanda

- `v7 = vendas_7d / 7` e `v30 = vendas_30d / 30` (unidades/dia).
- `aceleração = v7 / v30 − 1`. Positiva e `vendas_30d` acima do mínimo indicam aquecimento.
- Mínimo para afirmar tendência: `vendas_30d ≥ 30` (sug.). Abaixo: "amostra pequena".
- `trend_flag` / `sales_trend_flag`: use só conforme a descrição oficial do campo. Se não a tiver em mãos, trate como `sem dado`.
- GMV e vendas da EchoTik são estimativas da fonte; escreva "estimado".
- Google Trends é índice relativo (0-100). Serve para direção e sazonalidade; nunca converta em unidades vendidas.
- Concentração: `top3 = soma(GMV dos 3 maiores vendedores) / GMV total`. Acima de 60% (sug.) = mercado concentrado.

## 3. Preço

- Por categoria: p10, p50, p90 e CV (desvio/média).
- Posição do produto: percentil dentro da categoria.
- Faixa promissora: a faixa de preço com maior fatia de unidades e nota média ≥ 4,3 (limiar já usado neste projeto).
- Desconto: `1 − preço / preço_original`, só quando o original existir. Promoção recorrente em toda a categoria é preço normal, não oportunidade.
- Comissão por unidade: `preço × comissão%`.
- Ponto de equilíbrio com verba diária: `vendas_necessárias = verba_diária / comissão_un`.
- Liberar Ads só se margem ≥ 30% e comissão média por venda > 3× CPA estimado (regras do projeto).
- Não estime elasticidade sem série de preço e vendas do mesmo produto.

## 4. Sazonalidade

- `índice_mês = valor_mês / média_12m × 100` (média do ano = 100).
- Pico: índice ≥ 120. Vale: ≤ 80 (sug.).
- Mínimo: 12 meses; ideal 24 ou mais para separar tendência de sazonalidade (compare o mesmo mês ano a ano). Menos que 12 meses: `sem dado`.
- Calendário BR: calcule as datas no ano corrente, nunca fixe. Carnaval e Páscoa (móveis), Dia das Mães (2º domingo de maio), Dia dos Namorados (12/06), Dia dos Pais (2º domingo de agosto), Dia das Crianças (12/10), Consciência Negra (20/11), Black Friday (sexta após o Thanksgiving dos EUA), Natal, volta às aulas.
- Janela de preparação: 3 semanas antes do pico para estoque, links e criativos (regra do projeto). Entrar tarde encarece o CPM.

## 5. Reclamações e avaliações

- Rotule cada review com produto, vendedor e fonte **antes** de qualquer classificação por LLM.
- Considere só reviews com texto. Estado atual = últimos 180 dias (sug.). Mínimo de 30 reviews para dar percentuais (sug.).
- Sentimento: use a nota quando existir (≤ 2 = negativa). Use LLM só para tema, não para inventar nota.
- Temas (multi-rótulo, 1 contagem por review): defeito/qualidade, durabilidade, tamanho/ajuste, entrega/prazo, embalagem/avaria, atendimento/SAC, expectativa vs anúncio, preço/valor, segurança/saúde, falsificação/autenticidade.
- `share_tema = reviews_com_tema / reviews_total`.
- Separe o que é do produto, da logística e do vendedor: cada um pede ação diferente.
- Segurança, saúde e falsificação são críticos: sinalize mesmo com poucas ocorrências e encaminhe para a skill de compliance.
- Cite no máximo trechos curtos; nunca despeje reviews completos nem dados do autor.

## 6. Público

- Sinais permitidos: perguntas recorrentes nos comentários, hashtags, tipos de criador que vendem o produto, região, horário (se a fonte trouxer).
- Não infira atributos sensíveis (saúde, orientação sexual, religião, etnia, política). Não guarde nomes nem @handles; agregue (LGPD).
- Persona = hipótese. Exige ≥ 3 evidências independentes (sug.); senão marque `baixa`.
- Produza: dores, objeções, perguntas frequentes, ocasião de uso.

## 7. Nicho

- Base do projeto: `score = demanda − concorrência`. Versão transparente e reponderável (sug.):
  `score = 0,35·crescimento + 0,30·demanda + 0,25·(1 − concorrência) + 0,10·confiança`,
  cada termo normalizado de 0 a 1 por percentil dentro do conjunto comparado. Mostre os pesos.
- Subnicho em aquecimento: crescimento 30d > 40%.
- Entrada (regra do projeto): subnicho aquecido e ao menos 2 produtos ≥ 80°C, quando a "temperatura" existir nos dados.
- Alerta de saturação: criadores ou vendedores crescendo mais rápido que a demanda.
- Concorrência: nº de vendedores ativos, `top3` (§2), mediana de avaliações.

## 8. Confiança

| Nível | Critério (sug.) |
|---|---|
| alta | n ≥ 100, janela ≥ 90 dias, ≥ 2 fontes concordando |
| media | n ≥ 30 e uma fonte confiável |
| baixa | o resto; chame de hipótese |

Fontes que discordam: reporte as duas e o motivo provável (estimativa vs vitrine, T+1 vs tempo real, regiões). Não faça média entre elas.
