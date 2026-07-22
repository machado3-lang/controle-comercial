import json
import time
import httpx
from typing import Optional
from models import Empresa
from app.core.config import settings


API_BASE = settings.NOTAAS_API_URL


def _http_retry(method: str, url: str, empresa: Empresa, json_body: dict = None, params: dict = None, timeout: int = 30):
    headers = _get_headers(empresa)
    for tentativa in range(3):
        try:
            with httpx.Client(timeout=timeout) as client:
                if method == "GET":
                    resp = client.get(url, headers=headers, params=params)
                elif method == "POST":
                    resp = client.post(url, json=json_body, headers=headers)
                else:
                    resp = client.request(method, url, json=json_body, headers=headers, params=params)
                resp.raise_for_status()
                return resp
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 502, 503, 504) and tentativa < 2:
                time.sleep(2 ** tentativa)
                continue
            raise Exception(f"Erro NotaAs: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            if tentativa < 2:
                time.sleep(2 ** tentativa)
                continue
            raise


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
    resp = _http_retry("POST", url, empresa, json_body=payload, timeout=30)
    return resp.json()


def consultar_status(empresa: Empresa, invoice_id: str) -> dict:
    url = f"{API_BASE}/nfe/invoices/{invoice_id}/status"
    resp = _http_retry("GET", url, empresa, timeout=15)
    return resp.json()


def baixar_pdf(empresa: Empresa, invoice_id: str) -> bytes:
    url = f"{API_BASE}/nfe/invoices/{invoice_id}/danfe"
    resp = _http_retry("GET", url, empresa, timeout=30)
    return resp.content


def baixar_xml(empresa: Empresa, invoice_id: str) -> str:
    url = f"{API_BASE}/nfe/invoices/{invoice_id}/xml"
    resp = _http_retry("GET", url, empresa, timeout=30)
    return resp.text


def consultar_municipios(empresa: Empresa, uf: str = None) -> list:
    url = f"{API_BASE}/municipios"
    params = {}
    if uf:
        params["uf"] = uf
    resp = _http_retry("GET", url, empresa, params=params, timeout=15)
    return resp.json()


def cancelar_nfe(empresa: Empresa, invoice_id: str, motivo: str) -> dict:
    url = f"{API_BASE}/nfe/cancelar"
    payload = {"invoiceId": invoice_id, "justificativa": motivo}
    resp = _http_retry("POST", url, empresa, json_body=payload, timeout=30)
    return resp.json()


def montar_payload_nfe(
    empresa: Empresa,
    cliente,
    itens: list,
    numero_nfe: int = None,
    serie: int = None,
    modelo: int = 55,
    natureza_operacao: str = "Venda de mercadoria",
    cfop: str = None,
    finalidade: str = "normal",
    indicador_presenca: int = 1,
    data_emissao: str = None,
    data_saida: str = None,
) -> dict:
    cfop = cfop or empresa.cfop_padrao or "5102"
    destino_operacao = 1

    if cliente.estado and empresa.estado and cliente.estado != empresa.estado:
        destino_operacao = 2

    dest_cnpj = _limpar_doc(cliente.cpf_cnpj) if getattr(cliente, 'tipo_pessoa', None) == "juridica" else None
    dest_cpf = _limpar_doc(cliente.cpf_cnpj) if getattr(cliente, 'tipo_pessoa', None) == "fisica" else None
    dest_ie = _limpar_doc(cliente.inscricao_estadual) or None
    indicador_ie_map = {"contribuidor": 1, "isento": 2, "nao_contribuinte": 9}
    indicador_ie = indicador_ie_map.get(getattr(cliente, 'indicador_ie', None), 9)
    if indicador_ie == 9 and dest_ie:
        indicador_ie = 1

    tem_endereco = bool(cliente.endereco and cliente.bairro and cliente.cidade and cliente.estado)
    consumidor_final = 1 if (not dest_cnpj or indicador_ie == 9) else 0

    cod_mun = _to_int(_limpar_doc(cliente.codigo_ibge))
    cep_clean = _limpar_doc(cliente.cep) or ""

    finalidade_map = {"normal": 1, "complementar": 2, "ajuste": 3, "devolucao": 4, "credito": 4, "debito": 1}

    payload = {
        "modelo": modelo,
        "finalidade": finalidade_map.get(finalidade, 1),
        "naturezaOperacao": natureza_operacao,
        "destinoOperacao": destino_operacao,
        "presencaComprador": indicador_presenca,
        "consumidorFinal": consumidor_final,
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
                "codigoMunicipio": cod_mun,
                "cidade": cliente.cidade or "",
                "uf": cliente.estado or "",
                "cep": cep_clean,
            } if tem_endereco else None,
        },
        "items": [],
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

        payload["items"].append({
            "codigo": codigo,
            "descricao": descricao,
            "ncm": ncm,
            "cfop": cfop,
            "unidade": item.get("unidade", "UN"),
            "quantidade": qtd,
            "valorUnitario": preco,
            "valorTotal": total_item,
            "origem": item.get("origem", 0),
        })

    if data_emissao:
        payload["dataEmissao"] = data_emissao
    if data_saida:
        payload["dataSaida"] = data_saida

    total_nota = sum(i.get("valorTotal", 0) for i in payload["items"])
    payload["pagamentos"][0]["valor"] = total_nota

    # Tributos aproximados IBPT (Lei 12.741/2012)
    ali_fed = empresa.nfe_aliquota_federal or 0.0
    ali_est = empresa.nfe_aliquota_estadual or 0.0
    if ali_fed > 0 or ali_est > 0:
        v_fed = total_nota * ali_fed / 100
        v_est = total_nota * ali_est / 100
        v_tot = v_fed + v_est
        p_tot = ali_fed + ali_est
        payload["infCpl"] = (
            f"Total aproximado de tributos: R$ {v_tot:.2f} ({p_tot:.2f}%) "
            f"Federais R$ {v_fed:.2f} ({ali_fed:.2f}%) "
            f"Estaduais R$ {v_est:.2f} ({ali_est:.2f}%) . "
            f"Fonte IBPT."
        )

    return payload


def _to_int(val):
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


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
                    "origem": produto.origem or 0,
                })
            elif produto.tipo == "servico":
                itens_nfse.append(item)
            elif produto.tipo == "kit" and db:
                _explodir_kit(db, produto, item.quantidade or 1, itens_nfe, itens_nfse)

    if os:
        if os.valor_pecas and os.valor_pecas > 0:
            nomes_pecas = "Peças diversas"
            if os.pecas_utilizadas:
                try:
                    pecas_data = json.loads(os.pecas_utilizadas)
                    if isinstance(pecas_data, list):
                        nomes_pecas = "; ".join(p.get("nome", "") for p in pecas_data if p.get("nome"))
                except (json.JSONDecodeError, TypeError):
                    nomes_pecas = os.pecas_utilizadas[:280]
            itens_nfe.append({
                "produto_id": None,
                "descricao": nomes_pecas[:300],
                "ncm": "99999999",
                "unidade": "UN",
                "quantidade": 1,
                "preco_unitario": os.valor_pecas,
                "origem": 0,
            })

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
                "origem": insumo.origem or 0,
            })
        elif insumo.tipo == "servico":
            itens_nfse.append(insumo)
        elif insumo.tipo == "kit":
            _explodir_kit(db, insumo, qtd, itens_nfe, itens_nfse)


def explodir_itens_consolidacao(consolidacao, db) -> tuple:
    """Explode itens de uma consolidação em itens NFe (produtos) e NFSe (serviços)"""
    itens_nfe = []
    itens_nfse = []

    for item in consolidacao.itens:
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
                "origem": produto.origem or 0,
            })
        elif produto.tipo == "servico":
            itens_nfse.append(item)
        elif produto.tipo == "kit" and db:
            _explodir_kit(db, produto, item.quantidade or 1, itens_nfe, itens_nfse)

    return itens_nfe, itens_nfse


def explodir_itens_consolidacao(consolidacao=None, db=None) -> tuple:
    """Explode itens de uma consolidação separando produtos (NFe) e serviços (NFSe)"""
    itens_nfe = []
    itens_nfse = []

    if consolidacao:
        for item in consolidacao.itens:
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
                    "origem": produto.origem or 0,
                })
            elif produto.tipo == "servico":
                itens_nfse.append(item)
            elif produto.tipo == "kit" and db:
                _explodir_kit(db, produto, item.quantidade or 1, itens_nfe, itens_nfse)

    return itens_nfe, itens_nfse
