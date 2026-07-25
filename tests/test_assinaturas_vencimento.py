from datetime import date

import pytest

from models import Assinatura, ContaReceber, StatusConta
from routers import assinaturas as router_assinaturas
from tests.conftest import criar_cliente_teste


def _prox(a: Assinatura):
    return router_assinaturas._proximo_vencimento(a)


@pytest.mark.asyncio
async def test_ordenacao_vencimento_usa_data_inteira(authenticated_client, db_session, test_empresa):
    cliente = criar_cliente_teste(db_session)
    hoje = date.today()

    specs = [(25, 0, "ZVenc A25"), (5, 1, "ZVenc B05"), (15, 0, "ZVenc C15")]
    criados = []
    for i, (dia, mes, nome) in enumerate(specs, start=1):
        c = criar_cliente_teste(db_session, nome=nome, cpf_cnpj=f"11111{i:05d}")
        a = Assinatura(
            cliente_id=c.id, periodicidade=1, descricao=nome,
            valor=100, quantidade=1, data_inicio=hoje, data_fim=date(2030, 1, 1),
            dia_vencimento=dia, mes_vencimento=mes, situacao=1, travar_cobranca=False,
        )
        db_session.add(a)
        db_session.commit()
        db_session.refresh(a)
        criados.append(a)

    a_25, a_05, a_15 = criados

    resp = await authenticated_client.get("/assinaturas/?sort=vencimento&ordem=asc")
    assert resp.status_code == 200
    text = resp.text

    # ordem esperada pela data real de vencimento (crescente)
    esperados = sorted([a_25, a_05, a_15], key=lambda a: _prox(a))
    ordem_esperada = [f"ZVenc {n}" for n in ["A25", "B05", "C15"]]
    # monta pelos objetos ordenados
    ordem_esperada = []
    for a in esperados:
        ordem_esperada.append({a_25: "ZVenc A25", a_05: "ZVenc B05", a_15: "ZVenc C15"}[a])

    posicoes = [text.find(n) for n in ordem_esperada]
    assert posicoes == sorted(posicoes), f"ordem quebrada: {posicoes} esperado {ordem_esperada}"


@pytest.mark.asyncio
async def test_alterar_dia_ajusta_cobrancas_futuras(authenticated_client, db_session, test_empresa):
    cliente = criar_cliente_teste(db_session)
    a = Assinatura(
        cliente_id=cliente.id, periodicidade=1, descricao="Recorrente",
        valor=100, quantidade=1, data_inicio=date.today(), data_fim=date(2030, 1, 1),
        dia_vencimento=10, mes_vencimento=0, situacao=1, travar_cobranca=False,
    )
    db_session.add(a)
    db_session.commit()
    db_session.refresh(a)

    # cobranca futura ja gerada (dia 10 do mes que vem)
    futuro = date(date.today().year, date.today().month, 10)
    from routers.assinaturas import _add_months
    futuro = _add_months(futuro, 1)
    conta = ContaReceber(
        cliente_id=cliente.id, descricao="Mensal - Recorrente", valor=100,
        data_vencimento=futuro, status=StatusConta.PENDENTE,
        observacao=f"Cobrança automática - assinatura #{a.id}",
    )
    db_session.add(conta)
    db_session.commit()
    db_session.refresh(conta)
    assert conta.data_vencimento.day == 10

    resp = await authenticated_client.get(f"/assinaturas/{a.id}/editar")
    import re
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    csrf = m.group(1) if m else "x"

    r = await authenticated_client.post(f"/assinaturas/{a.id}/editar", data={
        "csrf_token": csrf,
        "cliente_id": str(cliente.id),
        "periodicidade": "1",
        "servico_id": "",
        "descricao": "Recorrente",
        "valor": "100",
        "quantidade": "1",
        "data_inicio": date.today().isoformat(),
        "data_fim": "2030-01-01",
        "dia_vencimento": "20",
        "mes_vencimento": "0",
        "situacao": "1",
        "fornecedor_id": "",
        "valor_revenda": "0",
        "numero_contrato": "",
        "observacao": "",
    })
    assert r.status_code in (200, 302, 303)

    db_session.expire_all()
    c2 = db_session.get(ContaReceber, conta.id)
    # dia ajustado para 20, mantendo mes/ano
    assert c2.data_vencimento.day == 20
    assert c2.data_vencimento.month == futuro.month
    assert c2.data_vencimento.year == futuro.year
