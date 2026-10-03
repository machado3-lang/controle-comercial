"""Regras de desconto compartilhadas entre pedidos, NFe e NFSe.

Regra unica do sistema: o desconto **nao** altera o preco unitario dos itens
quando o documento tem campo proprio de desconto (vDesc da NFe). Onde o
modelo nao tem esse campo (NFS-e), o desconto e rateado no valor unitario,
que e o unico meio de refleti-lo no documento.

Funcoes:
- `ratear_por_bruto`: divide um desconto entre dois "lados" (ex.: produtos
  que viram NFe e servicos que viram NFSe) proporcionalmente ao bruto.
- `distribuir_desconto`: aplica o desconto como `desconto` (vDesc) de cada
  item, proporcional ao total de cada um, sem perder centavos (o ultimo
  item recebe o residuo).
- `aplicar_desconto_unitario`: aplica o desconto reduzindo o valor
  unitario (usado na NFS-e, que nao tem vDesc por item).
"""
from decimal import Decimal, InvalidOperation


def _dec(valor) -> Decimal:
    try:
        return Decimal(str(valor if valor is not None else 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _bruto_item(item) -> Decimal:
    if isinstance(item, dict):
        qtd = _dec(item.get("quantidade", 1))
        preco = _dec(item.get("preco_unitario", item.get("valor_unitario", 0)))
        return (qtd * preco).quantize(Decimal("0.01"))
    qtd = _dec(getattr(item, "quantidade", 1))
    preco = _dec(getattr(item, "preco_unitario", getattr(item, "valor_unitario", 0)))
    return (qtd * preco).quantize(Decimal("0.01"))


def total_bruto(itens) -> Decimal:
    return sum((_bruto_item(i) for i in itens), Decimal("0"))


def ratear_por_bruto(bruto_a: Decimal, bruto_b: Decimal, desconto) -> tuple:
    """Divide `desconto` entre A e B proporcionalmente ao valor bruto.

    Ex.: pedido com produtos (NFe) e servicos (NFSe) — cada documento recebe
    a sua parte do desconto, e a soma fecha exatamente com o total.
    """
    desc = _dec(desconto)
    a, b = _dec(bruto_a), _dec(bruto_b)
    total = a + b
    if desc <= 0 or total <= 0:
        return Decimal("0"), Decimal("0")
    if desc > total:
        desc = total
    parte_a = (desc * a / total).quantize(Decimal("0.01"))
    return parte_a, (desc - parte_a).quantize(Decimal("0.01"))


def distribuir_desconto(itens, desconto) -> Decimal:
    """Distribui `desconto` proporcionalmente no campo `desconto` de cada item.

    Mantem preco unitario e total intactos (vDesc real, padrao NFe). Devolve
    o desconto efetivamente aplicado (ja limitado ao bruto).
    """
    desc = _dec(desconto)
    bruto = total_bruto(itens)
    if desc <= 0 or bruto <= 0 or not itens:
        for item in itens:
            if isinstance(item, dict):
                item["desconto"] = Decimal("0")
        return Decimal("0")
    if desc > bruto:
        desc = bruto

    restante = desc
    n = len(itens)
    for idx, item in enumerate(itens):
        item_bruto = _bruto_item(item)
        if n == 1 or idx == n - 1:
            v_desc = restante
        else:
            proporcao = (item_bruto / bruto) if bruto else Decimal("0")
            v_desc = (desc * proporcao).quantize(Decimal("0.01"))
            if v_desc > restante:
                v_desc = restante
            restante -= v_desc
        if isinstance(item, dict):
            item["desconto"] = v_desc
        else:
            try:
                item.desconto = v_desc
            except AttributeError:
                pass
    return desc


def calcular_valores_com_desconto(itens, desconto) -> list:
    """Mesma regra de `aplicar_desconto_unitario`, mas SEM alterar os itens.

    Devolve uma lista de tuplas (valor_unitario, valor_total) na mesma ordem
    de `itens`. Usado quando os itens sao objetos ORM que nao devem ser
    alterados (ex.: itens de um pedido que sera mantido intacto).
    """
    desc = _dec(desconto)
    bruto = total_bruto(itens)
    qtds = [_dec(i.get("quantidade", 1) if isinstance(i, dict) else getattr(i, "quantidade", 1)) for i in itens]
    units = [
        _dec(i.get("valor_unitario", i.get("preco_unitario", 0))) if isinstance(i, dict)
        else _dec(getattr(i, "preco_unitario", getattr(i, "valor_unitario", 0)))
        for i in itens
    ]
    if desc > 0 and bruto > 0:
        fator = Decimal("1") - (min(desc, bruto) / bruto)
        units = [(u * fator).quantize(Decimal("0.01")) for u in units]
    return [(u, (u * q).quantize(Decimal("0.01"))) for u, q in zip(units, qtds)]


def aplicar_desconto_unitario(itens, desconto) -> Decimal:
    """Aplica `desconto` reduzindo o valor unitario (modelo sem vDesc).

    Usado na NFS-e: `valor_unitario` e `valor_total` dos itens sao recalculados
    pelo fator (bruto - desconto) / bruto. Devolve o novo somatorio dos itens.
    """
    desc = _dec(desconto)
    bruto = total_bruto(itens)
    if not itens:
        return Decimal("0")
    if desc <= 0 or bruto <= 0:
        return bruto
    if desc > bruto:
        desc = bruto
    fator = Decimal("1") - (desc / bruto)

    novo_total = Decimal("0")
    for item in itens:
        if isinstance(item, dict):
            unit = (_dec(item.get("valor_unitario", item.get("preco_unitario", 0))) * fator).quantize(Decimal("0.01"))
            qtd = _dec(item.get("quantidade", 1))
            item["valor_unitario"] = unit
            item["preco_unitario"] = unit
            item["valor_total"] = (unit * qtd).quantize(Decimal("0.01"))
            novo_total += item["valor_total"]
        else:
            unit = (_dec(getattr(item, "preco_unitario", getattr(item, "valor_unitario", 0))) * fator).quantize(Decimal("0.01"))
            qtd = _dec(getattr(item, "quantidade", 1))
            try:
                item.preco_unitario = unit
                item.valor_unitario = unit
                item.total = (unit * qtd).quantize(Decimal("0.01"))
                novo_total += item.total
            except AttributeError:
                pass
    return novo_total
