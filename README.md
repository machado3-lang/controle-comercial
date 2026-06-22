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

## Admin Padrão
- Email: `admin@controle.com`
- Senha: `admin123`
- Execute `/auth/setup` para garantir permissões de admin

## Interface
- Tema dark (cyan, emerald, rose, amber)
- Glass cards com backdrop-blur
- Logo Control iZ chanfro
- Ícones Lucide

## Deploy
- Configurado para Railway
- Banco SQLite local, PostgreSQL no Railway
- Variáveis: `SECRET_KEY` (opcional)