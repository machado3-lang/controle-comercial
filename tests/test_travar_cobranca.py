import re
import pytest
from datetime import date

from models import Assinatura
from tests.conftest import criar_cliente_teste


@pytest.mark.asyncio
async def test_travar_cobranca_salva_via_form(authenticated_client, db_session, test_empresa):
    cliente = criar_cliente_teste(db_session)
    a = Assinatura(
        cliente_id=cliente.id, periodicidade=1, descricao="Teste trava",
        valor=100, quantidade=1, data_inicio=date.today(), data_fim=date(2030, 1, 1),
        dia_vencimento=10, mes_vencimento=0, situacao=1, travar_cobranca=False,
    )
    db_session.add(a)
    db_session.commit()
    db_session.refresh(a)

    # pega csrf do form de edicao
    resp = await authenticated_client.get(f"/assinaturas/{a.id}/editar")
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    csrf = m.group(1) if m else "x"
    tem_checkbox = 'name="travar_cobranca"' in resp.text
    print("CHECKBOX NO FORM:", tem_checkbox)

    # POST marcando a trava, simulando o form real (servico_id vazio = "Selecione...")
    r = await authenticated_client.post(f"/assinaturas/{a.id}/editar", data={
        "csrf_token": csrf,
        "cliente_id": str(cliente.id),
        "periodicidade": "1",
        "servico_id": "",
        "descricao": "Teste trava",
        "valor": "100",
        "quantidade": "1",
        "data_inicio": date.today().isoformat(),
        "data_fim": "2030-01-01",
        "dia_vencimento": "10",
        "mes_vencimento": "0",
        "situacao": "1",
        "fornecedor_id": "",
        "valor_revenda": "0",
        "numero_contrato": "",
        "observacao": "",
        "travar_cobranca": "1",
    })
    print("STATUS:", r.status_code)
    db_session.expire_all()
    a2 = db_session.get(Assinatura, a.id)
    print("TRAVAR APOS SAVE:", a2.travar_cobranca)
    assert tem_checkbox is True
    assert r.status_code in (200, 302, 303)
    assert a2.travar_cobranca is True
