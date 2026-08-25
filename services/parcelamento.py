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

from models import ContaReceber, ContaPagar, StatusConta
from sqlalchemy import or_

logger = logging.getLogger(__name__)

# Status considerados "emitidos" para NFe/NFSe ao decidir o numero do documento
# da cobranca (devem espelhar os usados na geracao de cobranca da OS).
_STATUS_EMITIDOS_NFE = {"issued", "queued", "pendente"}
_STATUS_EMITIDOS_NFSE = {"autorizada", "pendente", "em_processamento"}


def numero_documento_para_cobranca(pedido=None, consolidacao=None):
    """Retorna o numero do documento a ser usado no boleto da cobranca.

    Prioriza a nota fiscal (NFe/NFSe) vinculada ao pedido/consolidacao. So usa o
    numero do proprio pedido/consolidacao quando nao ha nota emitida (caso em que
    esse identificador faz mais sentido para rastrear a cobranca).
    """
    nfe = None
    nfse = None
    if pedido is not None:
        nfe = next((n for n in (pedido.nfes or []) if n.status in _STATUS_EMITIDOS_NFE and n.numero), None)
        nfse = pedido.nfse if (pedido.nfse and pedido.nfse.status in _STATUS_EMITIDOS_NFSE and pedido.nfse.numero) else None
    elif consolidacao is not None:
        nfe = next((n for n in (consolidacao.nfes or []) if n.status in _STATUS_EMITIDOS_NFE and n.numero), None)
        nfse = consolidacao.nfse if (consolidacao.nfse and consolidacao.nfse.status in _STATUS_EMITIDOS_NFSE and consolidacao.nfse.numero) else None
    if nfe:
        return str(nfe.numero)
    if nfse:
        return str(nfse.numero)
    return None


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
    os_id=None,
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
            os_id=os_id,
            numero_parcela=p["numero"],
            total_parcelas=num_parcelas,
            parcelamento_grupo=grupo,
            status=StatusConta.PENDENTE,
        )
        db.add(conta)
        contas.append(conta)
    return contas


def gerar_contas_pagar(
    db,
    *,
    fornecedor_id,
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
):
    """Cria N ContaPagar (parcelas) na sessao. NAO faz commit.

    Espelha gerar_contas_receber para o lado de contas a pagar.
    Retorna a lista de contas criadas (adicionadas via db.add).
    """
    try:
        num_parcelas = max(1, int(num_parcelas or 1))
    except (ValueError, TypeError):
        num_parcelas = 1
    parcelas = calcular_parcelas(valor_total, num_parcelas, primeiro_vencimento, intervalo_dias)
    return gerar_contas_pagar_parcelas(
        db,
        fornecedor_id=fornecedor_id,
        descricao=descricao,
        parcelas=parcelas,
        forma_pagamento=forma_pagamento,
        observacao=observacao,
        numero_documento=numero_documento,
        tipo_documento_id=tipo_documento_id,
        plano_conta_id=plano_conta_id,
    )


def gerar_contas_pagar_parcelas(
    db,
    *,
    fornecedor_id,
    descricao,
    parcelas,
    forma_pagamento=None,
    observacao=None,
    numero_documento=None,
    tipo_documento_id=None,
    plano_conta_id=None,
):
    """Cria N ContaPagar a partir de uma lista EXPLICITA de parcelas.

    Usado quando os valores/vencimentos ja sao conhecidos (ex.: duplicatas
    lidas do XML da NF-e ou informadas manualmente pelo usuario), sem rateio
    uniforme. Reaproveita o mesmo agrupamento (parcelamento_grupo) das demais
    contas a pagar. NAO faz commit.

    `parcelas`: lista de dicts {"numero", "valor" (Decimal/num), "vencimento" (date)}.
    Retorna a lista de contas criadas.
    """
    parcelas = list(parcelas or [])
    if not parcelas:
        return []
    total = len(parcelas)
    grupo = str(uuid.uuid4()) if total > 1 else None
    contas = []
    for idx, p in enumerate(parcelas, start=1):
        numero = p.get("numero") or idx
        sufixo = f" ({numero}/{total})" if total > 1 else ""
        conta = ContaPagar(
            fornecedor_id=fornecedor_id,
            descricao=f"{descricao}{sufixo}",
            valor=p["valor"],
            data_vencimento=p["vencimento"],
            forma_pagamento=forma_pagamento,
            observacao=observacao,
            numero_documento=numero_documento,
            tipo_documento_id=tipo_documento_id,
            plano_conta_id=plano_conta_id,
            numero_parcela=numero,
            total_parcelas=total,
            parcelamento_grupo=grupo,
            status=StatusConta.PENDENTE,
        )
        db.add(conta)
        contas.append(conta)
    return contas


def contas_receber_existentes(db, *, pedido_id=None, consolidacao_id=None, nfse_id=None, nfe_id=None):
    """Retorna as ContaReceber ja geradas para o documento informado.

    Usado para evitar cobranca em duplicidade quando o mesmo pedido/consolidacao
    e faturado mais de uma vez (ex.: finalizar o pedido e, depois, emitir a
    NFSe do mesmo pedido). Contas canceladas/excluidas nao contam.
    """
    filtros = []
    if pedido_id:
        filtros.append(ContaReceber.pedido_id == pedido_id)
    if consolidacao_id:
        filtros.append(ContaReceber.consolidacao_id == consolidacao_id)
    if nfse_id:
        filtros.append(ContaReceber.nfse_id == nfse_id)
    if nfe_id:
        filtros.append(ContaReceber.nfe_id == nfe_id)
    if not filtros:
        return []
    return (
        db.query(ContaReceber)
        .filter(or_(*filtros))
        .filter(ContaReceber.status.notin_([StatusConta.CANCELADO, StatusConta.EXCLUIDO]))
        .all()
    )


def _ids_vinculados(*, pedido=None, consolidacao=None, nfse=None, nfe=None):
    """Resolve, a partir das entidades de faturamento, o conjunto de IDs de
    pedido/consolidacao/notas fiscais relacionados. Usado para detectar cobrancas
    ja emitidas em QUALQUER um dos fluxos (pedido, consolidacao, NFe, NFSe) e
    evitar duplicidade entre eles.
    """
    pedido_ids, consolidacao_ids, nfe_ids, nfse_ids = set(), set(), set(), set()

    if pedido is not None:
        pedido_ids.add(pedido.id)
        if pedido.consolidacao_id:
            consolidacao_ids.add(pedido.consolidacao_id)
        if pedido.nfse is not None:
            nfse_ids.add(pedido.nfse.id)
        for n in (pedido.nfes or []):
            nfe_ids.add(n.id)

    if consolidacao is not None:
        consolidacao_ids.add(consolidacao.id)
        for p in (consolidacao.pedidos_origem or []):
            pedido_ids.add(p.id)
            if p.nfse is not None:
                nfse_ids.add(p.nfse.id)
            for n in (p.nfes or []):
                nfe_ids.add(n.id)
        if consolidacao.nfse is not None:
            nfse_ids.add(consolidacao.nfse.id)
        for n in (consolidacao.nfes or []):
            nfe_ids.add(n.id)

    if nfe is not None:
        nfe_ids.add(nfe.id)
        pedido = getattr(nfe, "pedido", None)
        if pedido is not None:
            pedido_ids.add(pedido.id)
            if pedido.consolidacao_id:
                consolidacao_ids.add(pedido.consolidacao_id)

    if nfse is not None:
        nfse_ids.add(nfse.id)
        pedido = getattr(nfse, "pedido", None)
        if pedido is not None:
            pedido_ids.add(pedido.id)
            if pedido.consolidacao_id:
                consolidacao_ids.add(pedido.consolidacao_id)
        consolidacao = getattr(nfse, "consolidacao", None)
        if consolidacao is not None:
            consolidacao_ids.add(consolidacao.id)

    return pedido_ids, consolidacao_ids, nfe_ids, nfse_ids


def contas_receber_existentes_para(db, *, pedido=None, consolidacao=None, nfse=None, nfe=None):
    """Retorna as ContaReceber ja geradas para o documento informado OU para
    qualquer um de seus vinculos (pedido, consolidacao, NFe, NFSe).

    Centraliza a regra anti-duplicidade entre os fluxos de faturamento: gerar
    cobranca pela NFe/NFSe nao deve duplicar a cobranca ja gerada pelo pedido ou
    pela consolidacao, e vice-versa. Contas canceladas/excluidas nao contam.
    """
    p_ids, c_ids, n_ids, s_ids = _ids_vinculados(
        pedido=pedido, consolidacao=consolidacao, nfse=nfse, nfe=nfe
    )
    filtros = []
    if p_ids:
        filtros.append(ContaReceber.pedido_id.in_(p_ids))
    if c_ids:
        filtros.append(ContaReceber.consolidacao_id.in_(c_ids))
    if n_ids:
        filtros.append(ContaReceber.nfe_id.in_(n_ids))
    if s_ids:
        filtros.append(ContaReceber.nfse_id.in_(s_ids))
    if not filtros:
        return []
    return (
        db.query(ContaReceber)
        .filter(or_(*filtros))
        .filter(ContaReceber.status.notin_([StatusConta.CANCELADO, StatusConta.EXCLUIDO]))
        .all()
    )


def emitir_boletos_contas(db, contas, forcar=False):
    """Emite boletos Sicoob para todas as contas informadas.

    As contas precisam estar commitadas (o nosso_numero usa conta.id).
    Contas que ja possuem boleto emitido sao ignoradas (evita duplicidade no
    banco), a menos que `forcar=True`.
    Retorna (qtd_ok, lista_de_erros).
    """
    from routers.sicoob import emitir_boleto  # import tardio (evita ciclo)

    ok = 0
    erros = []
    for conta in contas:
        if conta.boleto_emitido and not forcar:
            logger.info(
                "Boleto da conta %s ja emitido (nosso_numero=%s); emissao ignorada",
                conta.id, conta.nosso_numero,
            )
            continue
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
