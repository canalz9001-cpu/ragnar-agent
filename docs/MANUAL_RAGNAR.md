# PADRÃO REUTILIZÁVEL DE AUTOMAÇÃO PARA INSTAGRAM

**Projeto de referência:** Agente Ragnar — Ragnar One  
**Versão do documento:** 1.1 — personalizada para Ragnar  
**Data:** 21 de setembro de 2026

---

## 1. RESUMO DO AGENTE RAGNAR

O Ragnar é um agente de Instagram criado para trabalhar 24 horas por dia em quatro frentes:

1. **Conteúdo:** pesquisar temas, planejar publicações, criar roteiros, legendas, carrosséis e Stories.
2. **Publicação:** organizar uma fila e publicar conteúdo aprovado ou programado.
3. **Atendimento:** responder comentários e Direct, principalmente quando alguém demonstra interesse.
4. **Vendas:** identificar, qualificar e encaminhar leads para o site, WhatsApp ou atendimento humano.

### Objetivo comercial atual

No projeto Ragnar One, o Ragnar deve:

- trabalhar as dores reais do público: aplicativo travando em jogos, suporte que não responde, pouco conteúdo e travamentos em filmes e séries;
- usar o termo **streaming** na comunicação comercial;
- captar pessoas por comentários, incluindo o gatilho **“QUERO”**;
- conversar no Direct sem respostas repetitivas;
- classificar leads como **FRIO, MORNO ou QUENTE**;
- encaminhar oportunidades para `https://ragnarplay.online/`, WhatsApp ou atendimento humano;
- produzir uma publicação diária, inicialmente planejada para as **18h no horário de São Paulo**;
- analisar conteúdos e tendências do nicho para criar ideias próprias, sem copiar outros perfis e sem prometer viralização.

### Módulos planejados

- **Planner:** define temas, calendário e objetivo de cada conteúdo.
- **Creator:** cria gancho, roteiro, legenda, CTA e instruções visuais.
- **Image Agent:** prepara o pedido de imagem ou seleciona o material necessário.
- **Publisher:** controla fila, horário, aprovação e publicação.
- **Engagement Agent:** responde comentários e Direct e identifica leads.
- **Lead Scorer:** classifica o interesse em FRIO, MORNO ou QUENTE.
- **Auditor:** mede resultados e alimenta o próximo planejamento.
- **Human Handoff:** transfere conversas sensíveis, reclamações e leads quentes para uma pessoa.

### Base técnica planejada

- repositório GitHub do Ragnar: a definir; não reutilizar credenciais ou implantação de outro cliente;
- hospedagem: Railway;
- integração oficial: Meta/Instagram via webhook;
- inteligência de texto: OpenAI;
- banco de dados e fila: PostgreSQL;
- containerização: Docker;
- deduplicação de eventos, validação de assinatura da Meta e registro de auditoria.

### Estado atual em 21/09/2026

Este arquivo define a configuração do agente Ragnar. A edição do manual não ativa uma integração.
Não foram verificados repositório, Railway, credenciais, webhook ou conta Instagram do Ragnar.
Os incidentes do projeto usado como modelo não são diagnóstico deste novo agente.
Antes de considerar o atendimento ativo, conectar a conta correta e validar o fluxo comentário → Direct → site/WhatsApp.

### Identidade e oferta confirmadas pelo responsável

- Nome do agente: **Ragnar**.
- Instagram: **@ragnarplay1** — https://www.instagram.com/ragnarplay1/
- Marca: **Ragnar One**, conforme a logo enviada; manter os textos RAGNAR, ONE e o número 1.
- Site: https://ragnarplay.online/
- WhatsApp informado: **(34) 9134-1688**.
- Link montado exatamente com os dígitos informados: https://wa.me/553491341688
- Validação pendente: confirmar o número completo do WhatsApp antes da ativação. Não acrescentar dígitos por suposição.
- Logo: emblema metálico dourado e prateado, escudo e detalhes nórdicos; versão com fundo transparente solicitada.

| Plano | Valor total do período |
|---|---:|
| Mensal | R$ 25,00 |
| Trimestral | R$ 60,00 |
| Semestral | R$ 110,00 |
| Anual | R$ 190,00 |

Todos os planos incluem, conforme o banner: **2 dispositivos simultâneos, qualidade Full HD, suporte via WhatsApp e guia de programação**.
O trimestral aparece como “Mais popular”. O banner oferece teste grátis, mas não informa duração: não inventar duração, condições, descontos, catálogo ou garantias.

### Saudação no Direct para comentário “QUERO”

> Olá! 👋 Sou o Ragnar, assistente virtual da Ragnar One. Vi seu comentário e vim te ajudar! Você pode conhecer os planos e solicitar seu teste grátis pelo site: https://ragnarplay.online/
>
> Se preferir, fale com nossa equipe pelo WhatsApp: (34) 9134-1688 👉 https://wa.me/553491341688
>
> Temos planos a partir de R$ 25,00, com 2 dispositivos simultâneos. Você quer testar ou conhecer os planos?

A saudação deve oferecer **as duas opções na primeira mensagem**, sem exigir uma resposta prévia para entregar os links.
Usar texto com links; botões são opcionais quando suportados pela integração.
Enviar somente em resposta ao gatilho autorizado, respeitando a elegibilidade da integração oficial.
Não afirmar publicamente que enviou uma mensagem se o envio não foi confirmado.

---

## 2. O PADRÃO CERTO PARA ATENDER VÁRIOS INSTAGRAMS

Não crie umo Ragnar diferente copiando todo o código para cada cliente. Use este modelo:

```text
MOTOR ÚNICO DA AUTOMAÇÃO
├── conexão oficial com Instagram/Meta
├── criação de conteúdo
├── atendimento e qualificação
├── fila de publicação
├── auditoria e métricas
└── regras de segurança

CONFIGURAÇÃO POR CLIENTE
├── identidade e voz
├── público e oferta
├── dores e objeções
├── CTAs e destinos
├── horários e frequência
├── permissões de automação
├── credenciais isoladas
└── limites e assuntos proibidos
```

Cada perfil recebe um `client_id` e uma configuração própria. O motor lê essa configuração antes de criar conteúdo, responder ou publicar.

### Regra obrigatória de isolamento

Nunca misture entre clientes:

- tokens;
- IDs de conta;
- conversas e leads;
- imagens e documentos;
- tom de voz;
- ofertas, preços e links;
- métricas;
- histórico de aprovação.

---

## 3. ARQUIVO DE CONFIGURAÇÃO POR CLIENTE

Use este bloco como ficha padrão. Duplique somente esta configuração para cadastrar um novo Instagram.

```yaml
client:
  id: "ragnar"
  business_name: "Ragnar One"
  agent_name: "Ragnar"
  instagram_handle: "@ragnarplay1"
  timezone: "America/Sao_Paulo"
  status: "onboarding"

business:
  category: "streaming"
  products_services:
    - "Planos de streaming para 2 dispositivos simultâneos"
  main_goal: "captar_leads_e_vender"
  website: "https://ragnarplay.online/"
  whatsapp: "553491341688"
  whatsapp_display: "(34) 9134-1688"
  whatsapp_url: "https://wa.me/553491341688"
  whatsapp_verified: false
  currency: "BRL"
  plans:
    - {name: "Mensal", months: 1, price: 25.00}
    - {name: "Trimestral", months: 3, price: 60.00}
    - {name: "Semestral", months: 6, price: 110.00}
    - {name: "Anual", months: 12, price: 190.00}
  simultaneous_devices: 2
  quality: "Full HD"
  support: "WhatsApp"
  program_guide: true
  free_trial_duration: null

audience:
  primary: "Pessoas interessadas em streaming de TV, filmes e séries"
  pains:
    - "Aplicativo travando em jogos, filmes e séries"
    - "Suporte que não atende e pouco conteúdo"
  desires:
    - "Entretenimento com praticidade e suporte"
  objections:
    - "Receio de contratar sem testar"

brand_voice:
  tone: "direto, humano e comercial"
  rhythm: "frases curtas"
  uses_slang: false
  uses_emojis: "moderado"
  preferred_words:
    - "streaming"
  forbidden_words:
    - "IPTV"
  preferred_ctas:
    - "Chame no WhatsApp"
  real_examples:
    - "Quer conhecer os planos da Ragnar One? Comente QUERO."

content:
  pillars:
    - "dor e solução"
    - "educação"
    - "prova real"
    - "oferta"
    - "relacionamento"
  formats:
    - "reel"
    - "carousel"
    - "story"
  posts_per_week: 5
  publishing_hours:
    - "18:00"
  research_window_days: 14
  require_real_sources_for_current_topics: true

engagement:
  keyword_triggers:
    QUERO: "enviar_saudacao_com_site_e_whatsapp"
  keyword_case_insensitive: true
  keyword_match: "palavra_inteira"
  deduplicate_by: "client_id + comment_id"
  greeting_template: "Saudação definida na seção 1: enviar integralmente com ambos os links"
  requested_comment_to_dm: true
  whatsapp_validation_required_before_activation: true
  lead_scoring:
    cold: "curtiu ou fez pergunta genérica"
    warm: "perguntou preço, funcionamento ou disponibilidade"
    hot: "pediu link, teste, compra, agenda ou contato humano"
  max_automatic_followups: 2
  handoff_on:
    - "lead_quente"
    - "reclamacao"
    - "pagamento"
    - "cancelamento"
    - "duvida_que_exige_confirmacao"

automation:
  content_generation: true
  auto_reply_comments: false # ativar após conexão e teste; fluxo solicitado pelo responsável
  auto_reply_dm: false # estado operacional ainda não validado
  auto_publish: false
  approval_required: true
  daily_action_limit: 30
  pause_on_repeated_errors: true

compliance:
  never_invent_results: true
  never_promise_virality: true
  never_impersonate_human_without_disclosure_policy: true
  use_official_meta_api_only: true
  prohibited_topics:
    - "Promessas de estabilidade absoluta, resultados ou recursos não confirmados"

destinations:
  default_conversion: "whatsapp"
  hot_lead_destination: "atendimento_humano"
  support_destination: "fila_suporte"
```

### Credenciais: nunca colocar nesse arquivo

Tokens, senhas e chaves devem ficar como variáveis secretas do ambiente, separadas por cliente. Exemplo de nomes:

```text
CLIENT_RAGNAR_INSTAGRAM_ACCESS_TOKEN
CLIENT_RAGNAR_INSTAGRAM_ACCOUNT_ID
CLIENT_RAGNAR_META_APP_SECRET
OPENAI_API_KEY
DATABASE_URL
WEBHOOK_VERIFY_TOKEN
```

Nunca publique credenciais no GitHub, em prompts, planilhas compartilhadas ou arquivos enviados ao cliente.

---

## 4. FORMULÁRIO DE ENTRADA DE UM NOVO CLIENTE

Antes de automatizar qualquer conta, colete:

1. nome da empresa e @ do Instagram;
2. produto ou serviço vendido;
3. público principal;
4. objetivo: vender, captar leads, agendar, atender ou crescer;
5. três principais dores do cliente final;
6. ofertas, preços e condições reais;
7. site, WhatsApp ou link de conversão;
8. exemplos de textos que representam a voz da marca;
9. palavras que devem e não devem ser usadas;
10. frequência e formatos desejados;
11. quem aprova o conteúdo;
12. situações que sempre exigem atendimento humano;
13. acesso autorizado à conta profissional e aos ativos corretos da Meta;
14. permissão separada para responder, enviar DM e publicar;
15. horário, região e dias em que a automação pode atuar.

---

## 5. FLUXOS PADRÃO

### Fluxo A — criação e publicação

```text
Pesquisa permitida
→ Planner cria pauta
→ Creator produz rascunho
→ Auditor verifica fatos, voz e CTA
→ cliente aprova
→ Publisher agenda
→ Instagram publica
→ métricas são coletadas
→ aprendizado entra no próximo plano
```

Comece com aprovação humana obrigatória. Libere publicação automática somente depois que o perfil passar pelos testes e apresentar histórico estável.

### Fluxo B — comentário com palavra-chave

```text
Comentário contém “QUERO”
→ webhook recebe o evento
→ deduplicação evita resposta repetida
→ enviar saudação no Direct com site e WhatsApp na primeira mensagem
→ registrar resultado do envio
→ resposta pública curta, sem alegar envio quando houver falha
→ uma pergunta simples qualifica o interesse
→ lead recebe classificação
→ lead quente vai para atendimento humano
```

### Fluxo C — Direct

```text
Mensagem recebida
→ identificar intenção
→ consultar contexto do cliente
→ responder primeiro à pergunta
→ fazer no máximo uma pergunta por vez
→ registrar consentimento e etapa
→ encaminhar quando necessário
```

### Fluxo D — pesquisa do que funciona

```text
Dados da própria conta + tendências públicas verificáveis
→ comparar posts com a mediana da própria conta
→ identificar gancho, tema, formato e retenção
→ extrair o mecanismo, sem copiar o conteúdo
→ criar hipótese
→ testar em pequena escala
→ medir
→ manter, ajustar ou descartar
```

Isso é um ciclo de otimização; não é uma promessa de viralização nem uma IA que “aprende sozinha” sem dados. Para funcionar, o agente precisa receber métricas reais e registrar os resultados de cada teste.

---

## 6. BANCO DE DADOS MÍNIMO

Cada tabela deve conter `client_id` para impedir mistura entre contas.

| Tabela | Finalidade |
|---|---|
| `clients` | cadastro e estado de cada cliente |
| `brand_profiles` | voz, público, oferta e limites |
| `instagram_accounts` | IDs e estado da conexão, sem expor tokens |
| `content_ideas` | ideias, fontes e hipóteses |
| `content_items` | rascunhos, versões, aprovação e publicação |
| `publication_queue` | data, hora, prioridade e status |
| `contacts` | pessoas que interagiram com a conta |
| `conversations` | contexto e etapa do atendimento |
| `leads` | classificação, origem e destino |
| `events` | eventos recebidos do webhook e deduplicação |
| `metrics` | alcance, retenção, compartilhamentos e conversão |
| `audit_logs` | quem fez o quê, quando e em qual conta |

---

## 7. MODOS DE OPERAÇÃO

### Modo 1 — Assistente de conteúdo

- cria, revisa e pesquisa;
- não responde nem publica;
- ideal para o primeiro teste com um cliente.

### Modo 2 — Copiloto com aprovação

- cria conteúdo;
- sugere respostas;
- agenda somente após aprovação;
- recomendado para a fase inicial de produção.

### Modo 3 — Automação controlada

- responde casos simples;
- publica conteúdos dentro de regras aprovadas;
- pausa em erros e transfere casos sensíveis;
- só deve ser liberado depois dos testes.

### Modo 4 — Operação avançada

- vários perfis no mesmo motor;
- painéis, métricas, filas, permissões e cobrança por cliente;
- exige monitoramento, logs, backups e processo de suporte.

---

## 8. CHECKLIST PARA ATIVAR CADA PERFIL

### Marca e estratégia

- [ ] ficha do cliente preenchida;
- [ ] voz validada com exemplos reais;
- [ ] oferta, preço, CTA e links confirmados;
- [ ] assuntos proibidos definidos;
- [ ] calendário inicial aprovado.

### Conta e integração

- [ ] conta profissional correta identificada;
- [ ] ativos e permissões oficiais da Meta confirmados;
- [ ] token armazenado como segredo;
- [ ] webhook validado;
- [ ] evento de teste recebido uma única vez;
- [ ] renovação ou expiração de credencial monitorada.

### Atendimento

- [ ] palavra-chave testada;
- [ ] comentário e Direct não se repetem;
- [ ] classificação FRIO/MORNO/QUENTE validada;
- [ ] encaminhamento humano funcionando;
- [ ] reclamação, pagamento e cancelamento nunca ficam presos na IA.

### Publicação

- [ ] imagem ou vídeo acessível pela integração;
- [ ] legenda revisada;
- [ ] aprovação registrada;
- [ ] publicação de teste confirmada no perfil correto;
- [ ] falha não gera postagem duplicada;
- [ ] limite diário configurado.

### Segurança e operação

- [ ] nenhuma credencial está no GitHub;
- [ ] dados separados por `client_id`;
- [ ] logs sem conteúdo sensível desnecessário;
- [ ] botão ou variável de pausa geral funcionando;
- [ ] alertas de erro configurados;
- [ ] responsável humano definido.

---

## 9. ORDEM RECOMENDADA PARA TRANSFORMAR A RAGNAR EM PRODUTO

1. **Estabilizar o Ragnar do Ragnar One:** configurar e validar Railway, OpenAI, Meta, banco e webhook próprios.
2. **Validar um fluxo completo:** comentário “QUERO” → Direct → classificação → encaminhamento.
3. **Ativar o modo copiloto:** conteúdo criado automaticamente, mas publicado somente após aprovação.
4. **Medir por duas a quatro semanas:** erros, duplicações, respostas, leads, publicações e conversões.
5. **Separar código de configuração:** retirar do código tudo que é exclusivo do Ragnar One.
6. **Adicionar `client_id` em todas as tabelas, filas e logs.**
7. **Criar painel de cadastro:** novo cliente, voz, oferta, permissões e horários.
8. **Cadastrar um segundo perfil piloto:** com poucas permissões e aprovação obrigatória.
9. **Só depois liberar automação controlada para vários clientes.**

---

## 10. INDICADORES PARA ACOMPANHAR

### Conteúdo

- alcance a não seguidores;
- retenção inicial e tempo médio;
- compartilhamentos por alcance;
- salvamentos por alcance;
- seguidores gerados por alcance;
- resultado comparado com a mediana da própria conta.

### Atendimento

- tempo até a primeira resposta;
- porcentagem de perguntas resolvidas;
- quantidade de transferências humanas;
- respostas duplicadas ou incorretas;
- conversas abandonadas.

### Comercial

- leads por origem e conteúdo;
- FRIO, MORNO e QUENTE;
- cliques no link ou WhatsApp;
- testes, agendamentos ou pedidos iniciados;
- conversão confirmada pelo responsável comercial.

### Operação

- publicações concluídas e falhas;
- falhas de token, webhook e API;
- duplicações evitadas;
- custo de IA por cliente;
- incidentes e tempo de recuperação.

---

## 11. REGRA DE OURO

Uma automação profissional de Instagram não é apenas um robô que publica. Ela precisa unir:

```text
ESTRATÉGIA + VOZ DA MARCA + CONTEÚDO + ATENDIMENTO
+ DADOS REAIS + APROVAÇÃO + SEGURANÇA + MEDIÇÃO
```

O produto comercial deve ser o **motor padronizado**, enquanto cada cliente recebe uma **configuração isolada e personalizada**.

Para o Ragnar virar esse produto, o primeiro marco não é adicionar mais funções. É concluir com segurança um único ciclo real do Ragnar One, medir o resultado e então transformar as partes específicas em configurações reutilizáveis.
