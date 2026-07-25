"""Servico central de parcelamento de contas a receber.

Fonte unica de verdade para geracao de parcelas em todos os fluxos de
faturamento (pedidos, NF-e, NFS-e, consolidacoes e lancamento manual).

Regras:
- O valor total e rateado igualmente entre as parcelas; a diferenca de
  centavos e ajustada na ULTIMA parcela (padrao de ERPs).
- Cada parcela vira uma ContaReceber propria, com numero_parcela /
  total_parcelas e um parcelamento_grupo (UUID) comum.
- A descricao recebe o sufixo " (n/N)" quando ha mais de uma parcela.
"""
import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN

from models import ContaReceber, StatusConta

logger = logging.getLogger(__name__)


def calcular_parcelas(valor_total, num_parcelas, primeiro_vencimento, intervalo_dias=30):
    """Divide valor_total em num_parcelas com vencimentos escalonados.

    Retorna lista de dicts: {"numero", "valor" (Decimal), "vencimento" (date)}.
    Ajuste de centavos na ultima parcela.
    """
    valor_total = Decimal(str(valor_total or 0))
    try:
        num_parcelas = max(1, int(num_parcelas or 1))
    except (ValueError, TypeError):
        num_parcelas = 1
    try:
        intervalo_dias = max(1, int(intervalo_dias or 30))
    except (ValueError, TypeError):
        intervalo_dias = 30
    if not isinstance(primeiro_vencimento, date):
        primeiro_vencimento = date.today()

    base = (valor_total / num_parcelas).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    parcelas = []
    acumulado = Decimal("0")
    for i in range(1, num_parcelas + 1):
        valor = (valor_total - acumulado) if i == num_parcelas else base
        acumulado += valor
        vencimento = primeiro_vencimento + timedelta(days=intervalo_dias * (i - 1))
        parcelas.append({"numero": i, "valor": valor, "vencimento": vencimento})
    return parcelas


def gerar_contas_receber(
    db,
    *,
    cliente_id,
    descricao,
    valor_total,
    primeiro_vencimento,
    num_parcelas=1,
    intervalo_dias=30,
    forma_pagamento=None,
    observacao=None,
    numero_documento=None,
    tipo_documento_id=None,
    plano_conta_id=None,
    nfse_id=None,
    nfe_id=None,
    pedido_id=None,
    consolidacao_id=None,
):
    """Cria N ContaReceber (parcelas) na sessao. NAO faz commit.

    Retorna a lista de contas criadas (adicionadas via db.add).
    """
    try:
        num_parcelas = max(1, int(num_parcelas or 1))
    except (ValueError, TypeError):
        num_parcelas = 1
    parcelas = calcular_parcelas(valor_total, num_parcelas, primeiro_vencimento, intervalo_dias)
    grupo = str(uuid.uuid4()) if num_parcelas > 1 else None
    contas = []
    for p in parcelas:
        sufixo = f" ({p['numero']}/{num_parcelas})" if num_parcelas > 1 else ""
        conta = ContaReceber(
            cliente_id=cliente_id,
            descricao=f"{descricao}{sufixo}",
            valor=p["valor"],
            data_vencimento=p["vencimento"],
            forma_pagamento=forma_pagamento,
            observacao=observacao,
            numero_documento=numero_documento,
            tipo_documento_id=tipo_documento_id,
            plano_conta_id=plano_conta_id,
            nfse_id=nfse_id,
            nfe_id=nfe_id,
            pedido_id=pedido_id,
            consolidacao_id=consolidacao_id,
            numero_parcela=p["numero"],
            total_parcelas=num_parcelas,
            parcelamento_grupo=grupo,
            status=StatusConta.PENDENTE,
        )
        db.add(conta)
        contas.append(conta)
    return contas


def emitir_boletos_contas(db, contas):
    """Emite boletos Sicoob para todas as contas informadas.

    As contas precisam estar commitadas (o nosso_numero usa conta.id).
    Retorna (qtd_ok, lista_de_erros).
    """
    from routers.sicoob import emitir_boleto  # import tardio (evita ciclo)

    ok = 0
    erros = []
    for conta in contas:
        try:
            resultado = emitir_boleto(db, conta)
            if resultado.get("success"):
                ok += 1
            else:
                erros.append(
                    f"Parcela {conta.numero_parcela or 1}/{conta.total_parcelas or 1}: "
                    f"{resultado.get('error')}"
                )
        except Exception as e:
            logger.exception("Erro ao emitir boleto da conta %s", conta.id)
            erros.append(f"Parcela {conta.numero_parcela or 1}/{conta.total_parcelas or 1}: {e}")
    return ok, erros
