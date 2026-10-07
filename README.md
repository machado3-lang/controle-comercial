# Controle Comercial Cyber-SaaS

Sistema de gestão com interface dark mode (Tailwind CDN) com integração Bling/Sicoob.

## Funcionalidades

### Gestão de Usuários
- Login com email/senha
- Recuperação de senha (`/auth/esqueci-senha`)
- Permissões granulares por módulo (clientes, fornecedores, produtos, pedidos, ordens_servico, assinaturas, contas)
- Apenas administradores cadastram/editam usuários

### Módulos
- **Clientes** - Cadastro, listagem, integração Bling
- **Fornecedores** - Gestão de fornecedores
- **Produtos** - Estoque baixo/zerado alertas, margem automática, situação
- **Pedidos** - Abas Produtos/Serviços, vinculação de serviços aos itens
- **Ordens de Serviço** - OS com abas (cliente, equipamento, peças, serviços), status e controle
- **Assinaturas** - Gestão de assinaturas, status
- **Contas** - Receber/Pagar com integração Sicoob

### Integrações
- **Bling API v3** - OAuth 2.0, webhook, sincronização
- **Sicoob** - Emissão de boletos, sincronização de pagamentos, teste de token

### Backup
- Automático com timestamp na pasta `backups/`

## Instalação Local

```bash
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

## Documentação

| Documento | Assunto |
|---|---|
| [DOCUMENTACAO.md](DOCUMENTACAO.md) | Índice geral e visão do sistema. |
| [MOTOR_CONTABIL.md](MOTOR_CONTABIL.md) | **Próximo passo:** o que falta para contabilidade de verdade (partida dobrada, razão, competência) e os 5 bloqueios a decidir. |
| [DOCUMENTACAO_CLASSIFICACAO_CONTABIL.md](DOCUMENTACAO_CLASSIFICACAO_CONTABIL.md) | Classificação automática das contas a receber pelo plano de contas. |
| [DOCUMENTACAO_PEDIDOS.md](DOCUMENTACAO_PEDIDOS.md) | Pedidos de venda, agrupamento, consolidação e a correção da receita duplicada. |
| [DOCUMENTACAO_FATURAMENTO_COBRANCA.md](DOCUMENTACAO_FATURAMENTO_COBRANCA.md) | Fluxo de faturamento e cobrança. |
| [DOCUMENTACAO_BOLETOS.md](DOCUMENTACAO_BOLETOS.md) | Boletos Sicoob. |
| [DOCUMENTACAO_BACKUP.md](DOCUMENTACAO_BACKUP.md) | Backup e restore. |

## Conta administrativa

O usuário inicial é criado em `/auth/setup` com a senha **definida na
execução** — não existe senha padrão fixa no código.

> ⚠️ **Se `admin@controle.com` ainda responder a `admin123`, troque agora.**
> Esse par foi a senha padrão de versões anteriores e está publicado neste
> arquivo. Como o deploy é público, quem ler o repositório consegue entrar.
>
> ```bash
> python scripts/auditar_senhas_padrao.py   # aponta quem ainda usa a padrão
> ```
>
> Troque pela tela "Usuários" e não volte para a senha padrão.

## Interface
- Tema dark (cyan, emerald, rose, amber)
- Glass cards com backdrop-blur
- Logo Control iZ chanfro
- Ícones Lucide

## PDFs
- Logo Control iZ à esquerda, texto MultiCom centralizado
- Modal de visualização com botões Imprimir/Salvar
- Botões inativados para boletos cancelados/pago/baixa_solicitada

## Deploy
- Configurado para Railway via Procfile -> ash start.sh.
- start.sh roda lembic upgrade head **antes** do uvicorn (aplica as migrations
  de alteracao de tipo que o create_all do startup nao faz, ex.: testado_tecnico
  Boolean->VARCHAR e normalizacao do enum statusos). Em banco sem historico Alembic
  (criado via create_all), faz stamp no ultimo revision aditivo e aplica so as
  migrations de tipo.
- Banco SQLite local, PostgreSQL no Railway.
- Variaveis: SECRET_KEY (opcional), DATABASE_URL (Railway injeta do plugin Postgres).
- Variáveis: `SECRET_KEY` (opcional)
