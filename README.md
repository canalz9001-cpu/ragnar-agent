# Ragnar — @ragnarplay1

Primeira versão executável do atendimento inicial da Ragnar One.

## Implementado

- GET /healthz: processo disponível; não significa Instagram conectado.
- GET /readyz: configuração necessária presente; não substitui teste real de permissões.
- GET/POST /webhook: verificação Meta e validação HMAC SHA-256.
- Gatilho QUERO em comentários da conta configurada, sem distinguir maiúsculas.
- Saudação privada com site, WhatsApp e os quatro planos do banner.
- Saudação a DM de texto recebida, limitada a uma por usuário por dia.
- SQLite em volume persistente, deduplicação, fila, limite diário e pausa geral.
- Envios com resultado incerto não são repetidos automaticamente.
- Antes de publicar cada Reel aprovado, pesquisa tendências públicas recentes, compara
  com o desempenho das próprias publicações e otimiza a legenda sem copiar terceiros.
- A pesquisa é atualizada uma vez ao dia e a leitura de desempenho é armazenada por
  seis horas, reduzindo custo e chamadas desnecessárias.

## Ainda não implementado

Conversação livre com OpenAI, classificação de leads, encaminhamento humano automático
e geração autônoma de vídeos. O Ragnar melhora a legenda de vídeos aprovados e agenda a
publicação, mas não fabrica novos vídeos sozinho. O cliente pode contatar a equipe pelo
link do WhatsApp.

## Implantação no Railway

1. New Project → Deploy from GitHub repo → canalz9001-cpu/ragnar-agent.
2. Anexar volume em `/data`; manter uma réplica e um worker.
3. Configurar as variáveis de `.env.example` na aba Variables. Nunca salvar segredos no repositório.
4. Generate Domain. Verificar `/healthz`.
5. Confirmar o número do WhatsApp: foi informado (34) 9134-1688; nenhum dígito foi adicionado por suposição.
6. No aplicativo Meta, usar a integração **Instagram API with Instagram Login**, a conta profissional @ragnarplay1 e as permissões correspondentes a comentários e mensagens. Este código não usa token de Página/Facebook Login.
7. Configurar callback `https://DOMINIO-RAILWAY/webhook` e o mesmo `WEBHOOK_VERIFY_TOKEN`. Assinar comments e messages, conceder permissões e concluir requisitos de acesso aplicáveis à conta.
8. Preencher META_API_VERSION com versão suportada pelo app e INSTAGRAM_ACCOUNT_ID com ID numérico retornado pela integração (não o @).
9. Com número confirmado, volume e segredos prontos: WHATSAPP_CONFIRMED=true; AUTOMATION_ENABLED=true.
10. Testar comentário QUERO em postagem da conta, recebimento do Direct com ambos os links e repetição do mesmo evento sem novo envio. Confirmar na conta real antes de anunciar atendimento ativo.

Não há credenciais incluídas. Falta de credenciais não derruba o serviço: mantém a automação desativada. A fila só aceita eventos autenticados quando a configuração está pronta. Pausar: AUTOMATION_ENABLED=false.

## Operação e limitações

O processo envia somente saudações fixas; não depende de chave OpenAI nesta etapa. Respostas da Meta, permissões e elegibilidade precisam de teste integrado. A documentação da Meta não pôde ser consultada integralmente durante a preparação; confirmar o contrato de Private Replies antes da ativação.

Eventos com status `failed` ou `uncertain` exigem inspeção; não há reenvio automático. Nunca reenviar manualmente antes de conferir se a mensagem já chegou. O banco armazena IDs de eventos e destinatários, sem texto bruto de conversas. Configurar backup e retenção do volume antes de uso prolongado. Para múltiplas réplicas ou clientes, migrar fila para PostgreSQL.

## Testes locais

`python -m unittest discover -s tests -v`

Para rodar: `pip install -r requirements.txt` e `gunicorn --config gunicorn.conf.py app:application`.

## Referências

- [Railway: volumes](https://docs.railway.com/volumes)
- [Meta: Private Replies](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/private-replies/)
