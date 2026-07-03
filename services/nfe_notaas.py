import httpx
from typing import Optional
from models import Empresa


API_BASE = "https://platform.notaas.com.br/api/v1"


def _get_headers(empresa: Empresa) -> dict:
    return {
        "Content-Type": "application/json",
        "x-api-key": empresa.notaas_api_key or "",
    }


def _get_ambiente(empresa: Empresa) -> int:
    return int(empresa.notaas_ambiente or 2)


def emitir_nfe(empresa: Empresa, payload: dict) -> dict:
    url = f"{API_BASE}/nfe/emitir"
    payload["modelo"] = payload.get("modelo", 55)
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_get_headers(empresa))
        if resp.status_code == 202:
            return resp.json()
        raise Exception(f"Erro NotaAs: {resp.status_code} - {resp.text}")


def consultar_status(empresa: Empresa, invoice_id: str) -> dict:
    url = f"{API_BASE}/nfe/invoices/{invoice_id}/status"
    with httpx.Client(timeout=15) as client:
        resp = client.get(url, headers=_get_headers(empresa))
        if resp.status_code == 200:
            return resp.json()
        raise Exception(f"Erro NotaAs: {resp.status_code} - {resp.text}")


def baixar_pdf(empresa: Empresa, invoice_id: str) -> bytes:
    url = f"{API_BASE}/nfe/invoices/{invoice_id}/danfe"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_get_headers(empresa))
        if resp.status_code == 200:
            return resp.content
        raise Exception(f"Erro NotaAs: {resp.status_code} - {resp.text}")


def baixar_xml(empresa: Empresa, invoice_id: str) -> str:
    url = f"{API_BASE}/nfe/invoices/{invoice_id}/xml"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_get_headers(empresa))
        if resp.status_code == 200:
            return resp.text
        raise Exception(f"Erro NotaAs: {resp.status_code} - {resp.text}")


def consultar_municipios(empresa: Empresa, uf: str = None) -> list:
    url = f"{API_BASE}/municipios"
    params = {}
    if uf:
        params["uf"] = uf
    with httpx.Client(timeout=15) as client:
        resp = client.get(url, headers=_get_headers(empresa), params=params)
        if resp.status_code == 200:
            return resp.json()
        raise Exception(f"Erro NotaAs: {resp.status_code} - {resp.text}")


def cancelar_nfe(empresa: Empresa, invoice_id: str, motivo: str) -> dict:
    url = f"{API_BASE}/nfe/cancelar"
    payload = {"invoiceId": invoice_id, "justificativa": motivo}
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_get_headers(empresa))
        if resp.status_code in (200, 202):
            return resp.json()
        raise Exception(f"Erro NotaAs: {resp.status_code} - {resp.text}")


def montar_payload_nfe(
    empresa: Empresa,
    cliente,
    itens: list,
    numero_nfe: int,
    serie: int = 1,
    modelo: int = 55,
    natureza_operacao: str = "Venda de mercadoria",
    cfop: str = None,
) -> dict:
    cfop = cfop or empresa.cfop_padrao or "5102"
    destino_operacao = 1

    if cliente.estado and empresa.estado and cliente.estado != empresa.estado:
        destino_operacao = 2

    dest_cnpj = _limpar_doc(cliente.cpf_cnpj) if getattr(cliente, 'tipo_pessoa', None) == "juridica" else None
    dest_cpf = _limpar_doc(cliente.cpf_cnpj) if getattr(cliente, 'tipo_pessoa', None) == "fisica" else None
    dest_ie = _limpar_doc(cliente.inscricao_estadual) or None
    indicador_ie = 9
    if dest_ie:
        indicador_ie = 1

    tem_endereco = bool(cliente.endereco and cliente.bairro and cliente.cidade and cliente.estado)
    consumidor_final = 1 if not dest_cnpj else 0

    payload = {
        "modelo": modelo,
        "serie": serie,
        "numero": numero_nfe,
        "naturezaOperacao": natureza_operacao,
        "destinoOperacao": destino_operacao,
        "presencaComprador": 1,
        "consumidorFinal": consumidor_final,
        "emitente": {
            "endereco": {
                "codigoMunicipio": _limpar_doc(empresa.codigo_ibge) or None,
            },
        } if empresa.codigo_ibge else None,
        "dest": {
            "nome": cliente.nome or "Consumidor",
            "cnpj": dest_cnpj,
            "cpf": dest_cpf,
            "ie": dest_ie,
            "indicadorIE": indicador_ie,
            "email": cliente.email or None,
            "endereco": {
                "logradouro": cliente.endereco or "",
                "numero": "SN",
                "bairro": cliente.bairro or "",
                "cidade": cliente.cidade or "",
                "uf": cliente.estado or "",
                "cep": _limpar_doc(cliente.cep) or "",
                "codigoMunicipio": _limpar_doc(cliente.codigo_ibge) or None,
            } if tem_endereco else None,
        },
        "itens": [],
        "pagamentos": [
            {"tipoPagamento": "01", "valor": 0}
        ],
    }

    for i, item in enumerate(itens):
        descricao = item.get("descricao", "")
        ncm = _obter_ncm(item)
        qtd = float(item.get("quantidade", 1))
        preco = float(item.get("preco_unitario", 0))
        total_item = round(preco * qtd, 2)
        codigo = str(item.get("produto_id", i + 1))

        payload["itens"].append({
            "codigo": codigo,
            "descricao": descricao,
            "ncm": ncm,
            "cfop": cfop,
            "unidade": item.get("unidade", "UN"),
            "quantidade": qtd,
            "valorUnitario": preco,
            "valorTotal": total_item,
        })

    total_nota = sum(i.get("valorTotal", 0) for i in payload["itens"])
    payload["pagamentos"][0]["valor"] = total_nota

    return payload


def _limpar_doc(valor) -> Optional[str]:
    if not valor:
        return None
    return "".join(c for c in str(valor) if c.isdigit())


def _obter_ncm(item: dict) -> str:
    return item.get("ncm") or "99999999"


def explodir_itens(pedido=None, os=None, db=None) -> tuple:
    itens_nfe = []
    itens_nfse = []

    if pedido:
        for item in pedido.itens:
            produto = item.produto
            if not produto:
                continue
            if produto.tipo == "produto":
                itens_nfe.append({
                    "produto_id": produto.id,
                    "descricao": item.descricao or produto.nome,
                    "ncm": produto.ncm,
                    "unidade": produto.unidade or "UN",
                    "quantidade": item.quantidade or 1,
                    "preco_unitario": item.preco_unitario or 0,
                })
            elif produto.tipo == "servico":
                itens_nfse.append(item)
            elif produto.tipo == "kit" and db:
                _explodir_kit(db, produto, item.quantidade or 1, itens_nfe, itens_nfse)

    if os:
        pass

    return itens_nfe, itens_nfse


def _explodir_kit(db, produto, quantidade, itens_nfe, itens_nfse):
    from models import ProdutoComposicao
    composicoes = db.query(ProdutoComposicao).filter(
        ProdutoComposicao.produto_pai_id == produto.id
    ).all()
    for comp in composicoes:
        insumo = comp.insumo
        qtd = (comp.quantidade_padrao or 1) * quantidade
        if insumo.tipo == "produto":
            itens_nfe.append({
                "produto_id": insumo.id,
                "descricao": insumo.nome,
                "ncm": insumo.ncm,
                "unidade": insumo.unidade or "UN",
                "quantidade": qtd,
                "preco_unitario": insumo.preco,
            })
        elif insumo.tipo == "servico":
            itens_nfse.append(insumo)
        elif insumo.tipo == "kit":
            _explodir_kit(db, insumo, qtd, itens_nfe, itens_nfse)
