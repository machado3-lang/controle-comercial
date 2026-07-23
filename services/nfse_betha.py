"""
NFSe Betha Cloud - Padrão Nacional DPS
Web Service SOAP - RecepcionarDps / ConsultarStatusDps
Endpoint: https://nota-eletronica.betha.cloud/dps/ws
WSDL: https://nota-eletronica.betha.cloud/dps/ws/service.wsdl

IMPORTANTE:
- Homologação (tpAmb=2) está suspensa (E130) - usar tpAmb=1 (produção)
- Código IBGE Dourados: 5003702
- O serviço LC116 deve estar cadastrado no portal Betha
"""
import os
import logging
import warnings
import tempfile
from typing import Optional
from requests import Session
from requests.auth import HTTPBasicAuth
from datetime import datetime, timezone, timedelta

from services.cert_store import load_certificate, extract_cert_info, create_temp_pfx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
warnings.filterwarnings('ignore')

class NFSeBethaError(Exception):
    pass

BETHA_NFSE_URL = os.getenv('BETHA_NFSE_DPS_URL', 'https://nota-eletronica.betha.cloud/dps/ws')
BETHA_NFSE_CANCEL_URL = os.getenv('BETHA_NFSE_CANCEL_URL', BETHA_NFSE_URL)
BETHA_NFSE_REST_URL = os.getenv('BETHA_NFSE_REST_URL', 'https://nota-eletronica.betha.cloud/api/v1/nfse')
BETHA_RECOVER_PDF_URL = os.getenv('BETHA_RECOVER_PDF_URL', 'https://e-gov.betha.com.br/e-nota/recoverpdfservlet')

# API do Ambiente de Dados Nacional (ADN) / SEFIN
ADN_NFSE_URL = os.getenv('ADN_NFSE_URL', 'https://sefin.nfse.gov.br/SefinNacional')
ADN_DFE_URL = os.getenv('ADN_DFE_URL', 'https://adn.nfse.gov.br/contribuintes/dfe')

# Cache (por processo) do último NSU visto na distribuição DF-e do ADN.
# Permite varredura incremental — notas recém-autorizadas têm NSU recente,
# então não é preciso varrer desde o NSU 0 a cada busca.
_ADN_NSU_CACHE: dict = {'ultNSU': 0}

class BethaNfseService:
    def __init__(self, cert_path: str = None, cert_password: str = None, empresa=None):
        self.usuario = os.getenv('BETHA_USUARIO')
        self.senha = os.getenv('BETHA_SENHA')
        self.cert_path = cert_path or os.getenv('CERT_PATH', './certs/certificado.pfx')
        self.cert_password = cert_password or os.getenv('CERT_PASSWORD')
        self._temp_pfx_path = None
        if empresa:
            self.load_cert_from_empresa(empresa)
        if not self.usuario or not self.senha:
            raise NFSeBethaError("Credenciais Betha não configuradas no .env")

    def load_cert_from_empresa(self, empresa):
        """
        Carrega certificado A1 do armazenamento seguro (arquivo criptografado).
        Fallback para base64 no banco (compatibilidade).
        """
        # Tenta carregar do armazenamento seguro
        if getattr(empresa, 'cert_id', None):
            pfx_data = load_certificate('empresa', empresa.cert_id)
            if pfx_data:
                self._temp_pfx_path = create_temp_pfx('empresa', empresa.cert_id)
                self.cert_path = self._temp_pfx_path
                self.cert_password = empresa.cert_password or os.getenv('CERT_PASSWORD', '')
                info = extract_cert_info(pfx_data, empresa.cert_password or '')
                if info.get('valida'):
                    logger.info(f"Certificado NFSe carregado do armazenamento seguro. Válido até: {info['valida']}")
                return

        # Fallback: base64 no banco (modo legado)
        import base64
        if empresa.cert_base64:
            pfx = base64.b64decode(empresa.cert_base64)
            tmp = os.path.join(tempfile.gettempdir(), 'certificado.pfx')
            with open(tmp, 'wb') as f:
                f.write(pfx)
            self.cert_path = tmp
            self.cert_password = empresa.cert_password or ''
            logger.warning("Certificado carregado do banco (base64) - modo legado. Migre para armazenamento seguro.")
        elif empresa.cert_path:
            self.cert_path = empresa.cert_path
            self.cert_password = empresa.cert_password or os.getenv('CERT_PASSWORD', '')

    def _get_pem_combined(self) -> str:
        from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
        with open(self.cert_path, 'rb') as f:
            pfx_data = f.read()
        private_key, cert, _ = pkcs12.load_key_and_certificates(
            pfx_data, 
            password=self.cert_password.encode() if self.cert_password else None
        )
        combined = private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=NoEncryption()
        ) + cert.public_bytes(Encoding.PEM)
        tmp_path = os.path.join(tempfile.gettempdir(), 'cert_combined.pem')
        with open(tmp_path, 'wb') as f:
            f.write(combined)
        return tmp_path

    def _get_session(self) -> Session:
        session = Session()
        session.verify = False
        if os.path.exists(self.cert_path):
            pem_path = self._get_pem_combined()
            session.cert = pem_path
        session.auth = HTTPBasicAuth(self.usuario, self.senha)
        return session

    def __del__(self):
        # Limpa arquivo temporário se criado
        if self._temp_pfx_path and os.path.exists(self._temp_pfx_path):
            try:
                os.unlink(self._temp_pfx_path)
            except Exception:
                pass

    def gerar_id_dps(self, cmun: str, cnpj: str, serie: str, ndps: str) -> str:
        """Gera ID DPS no layout nacional (45 chars):
        DPS + cMun(7) + tipoInscrição(1) + inscrição(14) + série(5) + nDPS(15).
        Tipo de inscrição: 1=CPF, 2=CNPJ (a Betha valida que seja 1 ou 2 — E001).
        Para retentativas, varia-se apenas o campo série (mantendo nDPS inalterado)
        a fim de gerar um ID distinto (evita E050 sem pular o número da nota)."""
        serie = (serie or '1')[:1]
        doc = "".join(c for c in str(cnpj or '') if c.isdigit())
        tipo_insc = '1' if len(doc) == 11 else '2'
        doc = doc.zfill(14)
        return f'DPS{cmun}{tipo_insc}{doc}0000{serie}{ndps}'

    def enviar_dps(self, dps_xml: str, tpAmb: int = 1) -> dict:
        from lxml import etree
        try:
            logger.info(f"Enviando DPS para Betha (tpAmb={tpAmb})...")
            session = self._get_session()
            soap_xml = f'''<soapenv:Envelope xmlns="http://www.betha.com.br/e-nota-dps" xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
   <soapenv:Header/>
   <soapenv:Body>
      <RecepcionarDpsEnvio>
         {dps_xml}
      </RecepcionarDpsEnvio>
   </soapenv:Body>
</soapenv:Envelope>'''

            logger.info(f"SOAP XML enviado:\n{soap_xml[:3000]}")
            logger.info(f"URL: {BETHA_NFSE_URL}")

            response = session.post(
                BETHA_NFSE_URL,
                data=soap_xml.encode('utf-8'),
                headers={
                    'Content-Type': 'text/xml; charset=utf-8',
                },
                timeout=60
            )
            logger.info(f"Response HTTP {response.status_code}, headers: {dict(response.headers)}")
            if response.status_code >= 400:
                body_err = response.text[:1500]
                if response.status_code == 400 and not body_err.strip():
                    body_err = ("(corpo vazio) XML da requisição rejeitado pelo servidor - "
                                "geralmente causado por caractere especial não escapado (&, <, >) "
                                "em nome/endereço do cliente ou descrição do serviço")
                logger.error(f"Erro HTTP {response.status_code} da Betha Cloud (enviar_dps): {body_err}")
                raise NFSeBethaError(f"Erro HTTP {response.status_code} da Betha Cloud: {body_err}")
            response.raise_for_status()
            logger.info(f"HTTP {response.status_code}, raw:\n{response.text[:2000]}")
            root = etree.fromstring(response.content)
            protocolo_el = root.find('.//{http://www.betha.com.br/e-nota-dps}protocolo')
            if protocolo_el is None:
                protocolo_el = root.find('.//protocolo')
            if protocolo_el is not None:
                logger.info(f"Protocolo recebido: {protocolo_el.text}")
                return {'protocolo': protocolo_el.text, 'status': 'sucesso'}
            lista_msg = root.find('.//{http://www.betha.com.br/e-nota-dps}listaMensagens')
            if lista_msg is None:
                lista_msg = root.find('.//listaMensagens')
            if lista_msg is not None:
                mensagens = lista_msg.findall('{http://www.betha.com.br/e-nota-dps}mensagem') or lista_msg.findall('mensagem')
                erros = []
                for m in mensagens:
                    cod = m.find('{http://www.betha.com.br/e-nota-dps}codigo') or m.find('codigo')
                    msg = m.find('{http://www.betha.com.br/e-nota-dps}mensagem') or m.find('mensagem')
                    erros.append({'codigo': cod.text if cod is not None else 'N/A', 'mensagem': msg.text if msg is not None else 'N/A'})
                    if cod is not None:
                        logger.error(f"Erro DPS {cod.text}: {msg.text if msg is not None else 'N/A'}")
                return {'protocolo': None, 'erros': erros}
            raise NFSeBethaError("Protocolo não retornado")
        except NFSeBethaError:
            raise
        except Exception as e:
            logger.error(f"Erro SOAP: {e}")
            raise NFSeBethaError(f"Erro SOAP: {e}")

    def consultar_status(self, protocolo: str, tpAmb: int = 1) -> dict:
        import re
        """Consulta status da DPS enviada"""
        logger.info(f"Consultando status da DPS {protocolo}...")
        session = self._get_session()
        
        cmun = os.getenv('MUNICIPIO_CODIGO', '5003702')
        cnpj = os.getenv('BETHA_CNPJ', '13133714000110')
        
        soap_xml = f'''<soapenv:Envelope xmlns="http://www.betha.com.br/e-nota-dps" xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
   <soapenv:Header/>
   <soapenv:Body>
      <ConsultarStatusDpsEnvio>
         <tpAmb>{tpAmb}</tpAmb>
         <codigoIbge>{cmun}</codigoIbge>
         <cpfCnpjPrestador>{cnpj}</cpfCnpjPrestador>
         <protocolo>{protocolo}</protocolo>
         <tipoIntegracao>EMISSAO</tipoIntegracao>
      </ConsultarStatusDpsEnvio>
   </soapenv:Body>
</soapenv:Envelope>'''
        response = session.post(
            BETHA_NFSE_URL,
            data=soap_xml.encode('utf-8'),
            headers={'Content-Type': 'text/xml; charset=utf-8'},
            timeout=60
        )
        logger.info(f"ConsultarStatus HTTP {response.status_code}, headers: {dict(response.headers)}")
        if response.status_code >= 400:
            body_err = response.text[:1000]
            logger.error(f"Erro HTTP {response.status_code} da Betha Cloud: {body_err}")
            raise NFSeBethaError(f"Erro HTTP {response.status_code} da Betha Cloud: {body_err}")
        response.raise_for_status()
        text = response.text
        
        def _val(name):
            m = re.search(rf'<[^:>]*:?{name}[^>]*>([^<]*)</[^:>]*:?{name}[^>]*>', text)
            return m.group(1).strip() if m else None
        
        result = {
            'status': _val('statusProcessamento') or 'DESCONHECIDO',
            'data_hora': _val('dataHoraRecebimento'),
            'situacao_nfse': _val('situacaoNfse') or _val('situacao'),
        }
        
        msgs = re.findall(r'<[^:>]*:?mensagem[^>]*>([^<]+)</[^:>]*:?mensagem[^>]*>', text)
        msgs = list(dict.fromkeys(m.strip() for m in msgs if m.strip()))
        if msgs:
            result['erros'] = [{'mensagem': m} for m in msgs]
        
        emissao = re.search(r'<[^:>]*:?emissao[^>]*>(.*?)</[^:>]*:?emissao[^>]*>', text, re.DOTALL)
        if emissao:
            e = emissao.group(1)
            result['numero_nfse'] = _val('numeroNotaFiscal')
            result['chave_acesso'] = _val('chaveAcesso')
            result['numero_dps'] = _val('numeroDps')
            result['serie_dps'] = _val('serieDps')
            result['id_dps'] = _val('idDps')
            # Tenta extrair XML completo da NFSe (CDATA ou elemento)
            cdata = re.search(r'<!\[CDATA\[(.*?)\]\]>', text, re.DOTALL)
            if cdata:
                result['xml_documento'] = cdata.group(1).strip()
            else:
                # Tenta extrair qualquer XML dentro de compNfse ou dpsXml
                comp = re.search(r'<[^:>]*:?compNfse[^>]*>(.*?)</[^:>]*:?compNfse[^>]*>', text, re.DOTALL)
                if comp:
                    result['xml_documento'] = comp.group(1).strip()
                else:
                    dps_xml = re.search(r'<[^:>]*:?dpsXml[^>]*>(.*?)</[^:>]*:?dpsXml[^>]*>', text, re.DOTALL)
                    if dps_xml:
                        result['xml_documento'] = dps_xml.group(1).strip()
            # Tenta extrair URL do DANFSe
            url = _val('urlDanfse') or _val('danfseUrl')
            if url:
                result['url_danfse'] = url
            # Tenta extrair tokens de download do PDF (recoverpdfservlet)
            for param in ('p1', 'p2', 'p3', 'p4', 'param1', 'param2', 'param3', 'param4'):
                val = _val(param)
                if val:
                    result[f'pdf_{param}'] = val
            # Tenta extrair link direto do recoverpdf
            pdf_link = re.search(r'recoverpdfservlet[^"\']*', text)
            if pdf_link:
                result['url_danfse'] = pdf_link.group(0)
        
        return result

    def consultar_nfse_rest(self, numero: str, codigo_verificacao: str = None) -> dict:
        """Consulta dados da NFSe via REST API (Fly e-Nota) para obter URL do DANFSe"""
        try:
            session = self._get_session()
            
            params = {'numero': numero}
            if codigo_verificacao:
                params['codigoVerificacao'] = codigo_verificacao
            response = session.get(BETHA_NFSE_REST_URL, params=params, timeout=30)
            if response.status_code == 200:
                return response.json()
            logger.warning(f"REST API retornou HTTP {response.status_code}: {response.text[:500]}")
            return {}
        except Exception as e:
            logger.warning(f"Erro ao consultar REST API: {e}")
            return {}

    def obter_danfse_url(self, numero: str, codigo_verificacao: str = None,
                          pdf_params: dict = None) -> str:
        """Tenta obter URL do DANFSe da Betha"""
        # Tenta REST API primeiro
        dados = self.consultar_nfse_rest(numero, codigo_verificacao)
        url = dados.get('urlDanfse') or dados.get('linkDanfse') or dados.get('url')
        if url:
            if not url.startswith('http'):
                url = f"https://e-gov.betha.com.br{url}" if url.startswith('/') else url
            return url
        # Tenta tokens do PDF salvos do ConsultarStatus
        if pdf_params:
            params = '&'.join(f"{k}={v}" for k, v in pdf_params.items() if v)
            if params:
                return f"{BETHA_RECOVER_PDF_URL}?{params}&local=C"
        # Tenta URL padrão Fly Notas
        if codigo_verificacao:
            url_pattern = os.getenv('BETHA_DANFSE_URL_PATTERN',
                'https://nota-eletronica.betha.cloud/fly/notas/danfse/{codigo}')
            return url_pattern.replace('{codigo}', codigo_verificacao).replace('{numero}', numero)
        return None

    def _get_adn_session(self) -> Session:
        """Session apenas com certificado (sem basic auth) para ADN"""
        session = Session()
        session.verify = False
        
        if os.path.exists(self.cert_path):
            pem_path = self._get_pem_combined()
            session.cert = pem_path
        return session

    def listar_nfse_adn(self, data_inicio: str = None, data_fim: str = None,
                        max_paginas: int = 50, empresa_cnpj: str = None) -> list[dict]:
        """Lista NFS-e do ADN via API de distribuição (DF-e) por período.
        Endpoint: GET /contribuintes/dfe/{ultNSU}

        Retorna lista de dicts com chaveAcesso, dhEmi, valor, tomador, xml, etc.
        """
        import base64, gzip, re, time
        session = self._get_adn_session()
        resultados = []
        notas_brutas = []
        cancelamentos = set()
        ultNSU = 0
        pagina = 0
        logger.info(f"Listando NFS-e do ADN de {data_inicio} a {data_fim}...")

        while pagina < max_paginas:
            try:
                url = f"{ADN_DFE_URL}/{ultNSU}"
                logger.info(f"ADN DFE req (pag {pagina + 1}): NSU={ultNSU}")
                time.sleep(1)  # rate limit
                r = session.get(url, timeout=60)
                if r.status_code == 429:
                    logger.warning("ADN 429 rate limit, aguardando 5s...")
                    time.sleep(5)
                    r = session.get(url, timeout=60)
                if r.status_code == 404:
                    logger.info("ADN DFE 404 — fim da distribuição")
                    break
                if r.status_code != 200:
                    logger.warning(f"ADN DFE HTTP {r.status_code}: {r.text[:300]}")
                    break

                data = r.json()
                status = data.get('StatusProcessamento', '')
                if status != 'DOCUMENTOS_LOCALIZADOS':
                    logger.info(f"ADN status: {status}")
                    break

                lote = data.get('LoteDFe') or []
                logger.info(f"ADN página {pagina + 1}: {len(lote)} documentos")
                if not lote:
                    break

                nsu_max = max((int(df.get('NSU', 0)) for df in lote if df.get('NSU')), default=ultNSU)

                for df in lote:
                    chave = df.get('ChaveAcesso', '')
                    if not chave:
                        continue
                    nsu = df.get('NSU', 0)

                    # Extrai XML do conteúdo (base64 + gzip)
                    xml_nfse = None
                    xml_b64 = df.get('ArquivoXml')
                    if xml_b64:
                        try:
                            xml_nfse = gzip.decompress(base64.b64decode(xml_b64)).decode('utf-8')
                        except Exception:
                            try:
                                xml_nfse = base64.b64decode(xml_b64).decode('utf-8')
                            except Exception:
                                pass

                    # Extrai informações do XML
                    dh_emi = df.get('DataHoraGeracao', '')
                    valor = None
                    tomador_nome = None
                    tomador_cnpj = None
                    emitente_nome = None
                    emitente_cnpj = None
                    numero_nfse = None
                    c_stat = None
                    if xml_nfse:
                        m = re.search(r'<[^:>]*:?nNFSe[^>]*>(\d+)</', xml_nfse)
                        if m: numero_nfse = m.group(1)
                        m = re.search(r'<[^:>]*:?dhEmi[^>]*>([^<]+)</', xml_nfse)
                        if m: dh_emi = m.group(1)
                        m = re.search(r'<[^:>]*:?vBC[^>]*>([\d.]+)</', xml_nfse)
                        if m: valor = float(m.group(1))
                        # Tomador: <toma><CNPJ>...</CNPJ><xNome>...</xNome></toma>
                        toma_match = re.search(r'<toma>(.*?)</toma>', xml_nfse, re.DOTALL)
                        if toma_match:
                            toma_bloco = toma_match.group(1)
                            m1 = re.search(r'<xNome>(.*?)</', toma_bloco)
                            if m1: tomador_nome = m1.group(1)
                            m2 = re.search(r'<CNPJ>(\d+)</', toma_bloco)
                            if m2: tomador_cnpj = m2.group(1)
                            if not tomador_cnpj:
                                m2 = re.search(r'<CPF>(\d+)</', toma_bloco)
                                if m2: tomador_cnpj = m2.group(1)
                        # Emitente (prestador): <emit><CNPJ>...</CNPJ><xNome>...</xNome></emit>
                        emit_match = re.search(r'<emit>(.*?)</emit>', xml_nfse, re.DOTALL)
                        if emit_match:
                            emit_bloco = emit_match.group(1)
                            m1 = re.search(r'<xNome>(.*?)</', emit_bloco)
                            if m1: emitente_nome = m1.group(1)
                            m2 = re.search(r'<CNPJ>(\d+)</', emit_bloco)
                            if m2: emitente_cnpj = m2.group(1)
                            if not emitente_cnpj:
                                m2 = re.search(r'<CPF>(\d+)</', emit_bloco)
                                if m2: emitente_cnpj = m2.group(1)
                        m = re.search(r'<[^:>]*:?cStat[^>]*>(\d+)</', xml_nfse)
                        if m: c_stat = m.group(1)

                    # Detecta evento de cancelamento. NFS-e (padrão nacional ADN)
                    # usa tipoEvento 101101; NF-e usa 110111. No DFe, a chave do
                    # envelope referencia a NFS-e cancelada.
                    is_cancel = False
                    if xml_nfse:
                        if re.search(r'<tpEvento>\s*(110111|101101)\s*</tpEvento>', xml_nfse) \
                           or re.search(r'<tipoEvento>[^<]*101101', xml_nfse) \
                           or re.search(r'<descEvento>[^<]*Cancel', xml_nfse, re.IGNORECASE) \
                           or re.search(r'<cDescEvento>[^<]*Cancel', xml_nfse, re.IGNORECASE):
                            is_cancel = True
                    if is_cancel:
                        cancelamentos.add(chave)
                        continue

                    # Filtro por data
                    if data_inicio or data_fim:
                        data_doc = dh_emi[:10] if dh_emi else ''
                        if data_inicio and data_doc < data_inicio:
                            continue
                        if data_fim and data_doc > data_fim:
                            continue

                    # Classifica: emitida (nosso CNPJ é o emitente) ou recebida (nosso CNPJ é o tomador)
                    cnpj_clean = re.sub(r'\D', '', empresa_cnpj or '')
                    emit_clean = re.sub(r'\D', '', emitente_cnpj or '')
                    toma_clean = re.sub(r'\D', '', tomador_cnpj or '')
                    if emit_clean == cnpj_clean:
                        tipo = 'emitida'
                    elif toma_clean == cnpj_clean:
                        tipo = 'recebida'
                    else:
                        tipo = 'outra'

                    notas_brutas.append({
                        'chaveAcesso': chave,
                        'numero': numero_nfse,
                        'dhEmi': dh_emi,
                        'valor': valor,
                        'tomador_nome': tomador_nome,
                        'tomador_cnpj': tomador_cnpj,
                        'emitente_nome': emitente_nome,
                        'emitente_cnpj': emitente_cnpj,
                        'cStat': c_stat,
                        'NSU': nsu,
                        'xml': xml_nfse,
                        'tipo': tipo,
                    })

                if nsu_max == ultNSU:
                    break
                ultNSU = nsu_max
                pagina += 1

            except Exception as e:
                logger.error(f"Erro ADN DFE: {e}")
                import traceback
                logger.error(traceback.format_exc())
                break

        # Marca como canceladas as notas cujo evento de cancelamento foi encontrado
        # no DFe (rápido, para exibição imediata). A confirmação autoritativa via
        # SEFIN (tipoEvento 101101) é feita em background pela rota, para não
        # estourar o timeout do proxy em períodos com muitas notas.
        for n in notas_brutas:
            n['cancelada'] = n['chaveAcesso'] in cancelamentos
            resultados.append(n)

        logger.info(f"ADN retornou {len(resultados)} NFS-e no período ({len(cancelamentos)} canceladas)")
        return resultados

    def _varrer_dfe_adn(self, chave: str, max_paginas: int = 80,
                        nsu_inicial: int = 0) -> Optional[str]:
        """Varre a distribuição DF-e do Ambiente Nacional (adn.nfse.gov.br),
        retornando o XML autorizado (infNFSe) da chave informada ou None.
        `nsu_inicial` permite varredura incremental (a partir do último NSU já
        visto), muito mais rápida para notas recém-autorizadas (NSU recente).
        Erros transitórios (429/5xx) são retentados em vez de abortar."""
        import base64, gzip, time
        session = self._get_adn_session()
        ultNSU = int(nsu_inicial or 0)
        erros_seguidos = 0
        docs_vistos = 0
        pagina = 0
        while pagina < max_paginas:
            pagina += 1
            url = f"{ADN_DFE_URL}/{ultNSU}"
            try:
                time.sleep(0.3)  # rate limit
                r = session.get(url, timeout=60)
                if r.status_code == 429:
                    logger.info(f"ADN DF-e 429 (NSU {ultNSU}); aguardando 5s...")
                    time.sleep(5)
                    continue
                if r.status_code != 200:
                    erros_seguidos += 1
                    logger.warning(f"ADN DF-e HTTP {r.status_code} (NSU {ultNSU}): {r.text[:200]}")
                    if erros_seguidos >= 3:
                        break
                    time.sleep(2)
                    continue
                erros_seguidos = 0
                data = r.json()
                status_proc = data.get('StatusProcessamento')
                if status_proc != 'DOCUMENTOS_LOCALIZADOS':
                    logger.info(f"ADN DF-e status '{status_proc}' (NSU {ultNSU}, pág {pagina})")
                    break
                lote = data.get('LoteDFe') or []
                if not lote:
                    break
                docs_vistos += len(lote)
                nsu_max = max((int(df.get('NSU', 0)) for df in lote if df.get('NSU')),
                              default=ultNSU)
                for df in lote:
                    if df.get('ChaveAcesso') != chave:
                        continue
                    xml_b64 = df.get('ArquivoXml')
                    if not xml_b64:
                        continue
                    try:
                        xml = gzip.decompress(base64.b64decode(xml_b64)).decode('utf-8')
                    except Exception:
                        try:
                            xml = base64.b64decode(xml_b64).decode('utf-8')
                        except Exception:
                            continue
                    # Só retorna o documento NFS-e autorizado (infNFSe); ignora
                    # eventos (cancelamento etc.) que compartilham a mesma chave.
                    if xml and 'infNFSe' in xml and 'sped.fazenda.gov.br/nfse' in xml:
                        logger.info(f"XML nacional localizado no ADN (pág {pagina}, NSU {df.get('NSU')})")
                        _ADN_NSU_CACHE['ultNSU'] = max(_ADN_NSU_CACHE.get('ultNSU', 0), nsu_max)
                        return xml
                # Atualiza cache do último NSU visto (para varreduras incrementais)
                _ADN_NSU_CACHE['ultNSU'] = max(_ADN_NSU_CACHE.get('ultNSU', 0), nsu_max)
                if nsu_max == ultNSU:
                    break
                ultNSU = nsu_max
            except Exception as e:
                erros_seguidos += 1
                logger.warning(f"Erro ADN DF-e pág {pagina} (NSU {ultNSU}): {e}")
                if erros_seguidos >= 3:
                    break
                time.sleep(2)
        logger.info(f"ADN DF-e: chave não localizada ({pagina} págs varridas, "
                    f"{docs_vistos} docs, NSU inicial {nsu_inicial}, NSU final {ultNSU})")
        return None

    def _obter_xml_sefin(self, chave: str) -> Optional[str]:
        """Busca direta do XML nacional no SEFIN por chave de acesso:
        GET /SefinNacional/nfse/{chave} → JSON com nfseXmlGZipB64.
        Muito mais rápido e confiável que varrer a distribuição DF-e."""
        import base64, gzip
        try:
            session = self._get_adn_session()
            r = session.get(f"{ADN_NFSE_URL}/nfse/{chave}", timeout=30)
            logger.info(f"SEFIN /nfse/(chave) => HTTP {r.status_code}")
            if r.status_code != 200:
                logger.info(f"SEFIN body: {r.text[:300]}")
                return None
            data = r.json()
            b64 = data.get('nfseXmlGZipB64')
            if not b64:
                logger.info(f"SEFIN 200 sem nfseXmlGZipB64; keys: {list(data.keys())[:10]}")
                return None
            try:
                xml = gzip.decompress(base64.b64decode(b64)).decode('utf-8')
            except Exception:
                xml = base64.b64decode(b64).decode('utf-8')
            if xml and 'infNFSe' in xml:
                logger.info("XML nacional obtido do SEFIN por chave")
                return xml
        except Exception as e:
            logger.warning(f"SEFIN por chave falhou: {e}")
        return None

    def _obter_xml_adn_por_chave(self, chave: str) -> Optional[str]:
        """Busca direta do DF-e no ADN por chave de acesso:
        GET /contribuintes/nfse/{chaveAcesso} — sem varredura de NSU.
        Retorna o XML nacional (infNFSe) ou None."""
        import base64, gzip
        base = ADN_DFE_URL.rsplit('/', 1)[0]  # .../contribuintes
        session = self._get_adn_session()
        for rota in (f"{base}/nfse/{chave}", f"{base}/NFSe/{chave}"):
            try:
                r = session.get(rota, timeout=30)
                logger.info(f"ADN por chave {rota.rsplit('/', 2)[-2]}/(chave) => HTTP {r.status_code}")
                if r.status_code != 200:
                    if r.status_code not in (404,):
                        logger.info(f"ADN por chave body: {r.text[:300]}")
                    continue
                data = r.json()
                # Formatos possíveis: doc único, lote, ou campo direto
                candidatos = []
                if isinstance(data, dict):
                    candidatos.append(data.get('ArquivoXml'))
                    candidatos.append(data.get('nfseXmlGZipB64'))
                    for df in (data.get('LoteDFe') or []):
                        candidatos.append(df.get('ArquivoXml'))
                elif isinstance(data, list):
                    for df in data:
                        if isinstance(df, dict):
                            candidatos.append(df.get('ArquivoXml'))
                for b64 in candidatos:
                    if not b64:
                        continue
                    try:
                        xml = gzip.decompress(base64.b64decode(b64)).decode('utf-8')
                    except Exception:
                        try:
                            xml = base64.b64decode(b64).decode('utf-8')
                        except Exception:
                            continue
                    if xml and 'infNFSe' in xml and 'sped.fazenda.gov.br/nfse' in xml:
                        logger.info("XML nacional obtido do ADN por chave de acesso")
                        return xml
            except Exception as e:
                logger.warning(f"ADN por chave falhou ({rota}): {e}")
        return None

    def obter_xml_nacional_por_chave(self, chave: str, max_paginas: int = 80,
                                    tentativas: int = 1, intervalo: float = 0) -> Optional[str]:
        """Obtém o XML da NFS-e Nacional (infNFSe+DPS) de UMA nota pela chave de
        acesso. Fonte primária: Ambiente Nacional (adn.nfse.gov.br).
        Estratégia (da mais direta para a mais custosa):
        1) ADN GET /contribuintes/nfse/{chave} — direto ao ponto, sem varredura;
        2) Varredura incremental do DF-e a partir do último NSU em cache
           (rápida — notas novas têm NSU recente);
        3) Varredura completa do DF-e desde o NSU 0;
        4) SEFIN GET /nfse/{chave} (último recurso; historicamente retorna 403).
        Esta é a única fonte válida do XML padrão nacional usado para gerar o
        DANFSe (brazilfiscalreport). Não usa XML da Betha.

        `tentativas`/`intervalo` permitem re-tentar enquanto o Ambiente Nacional
        ainda está propagando o documento (ele costuma atrasar alguns segundos
        após a autorização na prefeitura)."""
        import time
        if not chave:
            return None
        logger.info(f"Buscando XML nacional para chave {chave[:20]}...")
        for tent in range(max(tentativas, 1)):
            # 1) ADN direto por chave de acesso (sem varredura)
            xml = self._obter_xml_adn_por_chave(chave)
            if xml:
                return xml
            # 2) Varredura incremental (a partir do último NSU visto neste processo)
            nsu_cache = _ADN_NSU_CACHE.get('ultNSU', 0)
            if nsu_cache > 0:
                xml = self._varrer_dfe_adn(chave, max_paginas, nsu_inicial=nsu_cache)
                if xml:
                    return xml
            # 3) Varredura completa desde o NSU 0
            xml = self._varrer_dfe_adn(chave, max_paginas)
            if xml:
                return xml
            # 4) SEFIN direto por chave (último recurso)
            xml = self._obter_xml_sefin(chave)
            if xml:
                return xml
            if tent < max(tentativas, 1) - 1 and intervalo > 0:
                logger.info(f"XML nacional não localizado na tentativa {tent + 1}; "
                            f"aguardando {intervalo}s para nova tentativa...")
                time.sleep(intervalo)
        logger.info("XML nacional não localizado (distribuição DF-e e SEFIN)")
        return None

    def consultar_situacao_nfse(self, numero_nfse: str, codigo_verificacao: str = None) -> dict:
        """Consulta situação real via ADN SEFIN — GET /nfse + GET /eventos"""
        import json, re, base64, gzip
        if not codigo_verificacao:
            return {'situacao': None, 'aviso': 'sem chave'}
        chave = codigo_verificacao
        logger.info(f"Consultando NFSe {numero_nfse} no SEFIN ({chave[:10]}...)...")
        session = self._get_adn_session()
        
        situacao = 'normal'

        try:
            # 1) GET /nfse/{chaveAcesso} — retorna JSON com XML compactado
            resp = session.get(f"{ADN_NFSE_URL}/nfse/{chave}", timeout=30)
            if resp.status_code == 404:
                return {'situacao': situacao if situacao == 'cancelada' else None, 'aviso': 'SEFIN 404'}
            if resp.status_code != 200:
                return {'situacao': situacao if situacao == 'cancelada' else None, 'aviso': f'SEFIN HTTP {resp.status_code}'}

            data = resp.json()
            # Extrai XML da NFSe
            xml_nfse = None
            xml_b64 = data.get('nfseXmlGZipB64')
            if xml_b64:
                try: xml_nfse = gzip.decompress(base64.b64decode(xml_b64)).decode('utf-8')
                except Exception as e:
                    logger.warning(f"Erro ao decodificar XML NFSe: {e}")

            # 2) Busca evento de cancelamento no SEFIN (tipoEvento 101101 = cancelamento)
            if situacao == 'normal':
                for tp in ('101101', '101103', '202201', '202205'):
                    ev_url = f"{ADN_NFSE_URL}/nfse/{chave}/eventos/{tp}/1"
                    try:
                        ev_r = session.get(ev_url, timeout=30)
                        if ev_r.status_code == 200:
                            ev_data = ev_r.json()
                            ev_xml_b64 = ev_data.get('eventoXmlGZipB64')
                            if ev_xml_b64:
                                try:
                                    ev_xml = gzip.decompress(base64.b64decode(ev_xml_b64)).decode('utf-8')
                                    logger.info(f"Evento SEFIN {tp} XML: {ev_xml[:500]}")
                                except Exception:
                                    pass
                            # tipoEvento 101101 é exclusivamente cancelamento
                            situacao = 'cancelada'
                            logger.info(f"Cancelamento detectado via evento SEFIN {tp}")
                            break
                        else:
                            logger.info(f"SEFIN evento {tp} => HTTP {ev_r.status_code}")
                    except Exception as e:
                        logger.warning(f"SEFIN evento {tp} exception: {e}")

            # 3) Verifica cStat/tpEvento no XML da NFSe (fallback)
            if situacao == 'normal' and xml_nfse:
                if re.search(r'<[^:>]*:?cStat[^>]*>10[12]</', xml_nfse) or \
                   re.search(r'<[^:>]*:?tpEvento[^>]*>110111</', xml_nfse):
                    situacao = 'cancelada'
                    logger.info(f"Situação detectada como cancelada via XML NFSe")

            return {
                'situacao': situacao,
                'xml': xml_nfse,
                'chaveAcesso': chave,
                'eventos_debug': 'cancelada' if situacao == 'cancelada' else 'sem_eventos_cancelamento',
                'codigo': '2' if situacao == 'cancelada' else '1',
            }
        except Exception as e:
            logger.error(f"Erro consulta situação: {e}")
            return {
                'situacao': situacao if situacao == 'cancelada' else None,
                'erro': str(e),
                'eventos_debug': f'{situacao}_erro:{e}',
            }

    def cancelar_dps(self, tpAmb: int = 1, codigo_cancelamento: str = "1",
                      numero_nfse: str = None, protocolo: str = None) -> dict:
        """Cancela uma NFS-e via CancelarDpsEnvio"""
        import re
        ref = numero_nfse or protocolo or '?'
        tag = 'numeroNfse' if numero_nfse else 'protocolo'
        val = numero_nfse or protocolo
        logger.info(f"CancelarDpsEnvio {tag}={ref}...")
        session = self._get_session()
        
        cmun = os.getenv('MUNICIPIO_CODIGO', '5003702')
        cnpj = os.getenv('BETHA_CNPJ', '13133714000110')

        soap_xml = f'''<soapenv:Envelope xmlns="http://www.betha.com.br/e-nota-dps" xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
   <soapenv:Header/>
   <soapenv:Body>
      <CancelarDpsEnvio>
         <tpAmb>{tpAmb}</tpAmb>
         <codigoIbge>{cmun}</codigoIbge>
         <cpfCnpjPrestador>{cnpj}</cpfCnpjPrestador>
         <{tag}>{val}</{tag}>
         <codigoCancelamento>{codigo_cancelamento}</codigoCancelamento>
      </CancelarDpsEnvio>
   </soapenv:Body>
</soapenv:Envelope>'''

        try:
            response = session.post(
                BETHA_NFSE_URL,
                data=soap_xml.encode('utf-8'),
                headers={'Content-Type': 'text/xml; charset=utf-8'},
                timeout=60
            )
            if response.status_code >= 400:
                body_err = response.text[:1500]
                logger.error(f"Erro HTTP {response.status_code} da Betha Cloud (enviar_dps): {body_err}")
                raise NFSeBethaError(f"Erro HTTP {response.status_code} da Betha Cloud: {body_err}")
            response.raise_for_status()
            text = response.text
            logger.info(f"Cancelamento response: {text[:2000]}")

            def _v(name):
                m = re.search(rf'<[^:>]*:?{name}[^>]*>([^<]*)</[^:>]*:?{name}[^>]*>', text)
                return m.group(1).strip() if m else None

            if 'sucesso' in text.lower() or _v('CancelamentoHomologado'):
                return {'sucesso': True, 'protocolo': _v('protocolo')}
            msgs = re.findall(r'<[^:>]*:?mensagem[^>]*>([^<]+)</[^:>]*:?mensagem[^>]*>', text)
            msgs = list(dict.fromkeys(m.strip() for m in msgs if m.strip()))
            return {'sucesso': False, 'erros': [{'mensagem': m} for m in msgs] if msgs else [{'mensagem': 'Erro desconhecido no cancelamento'}]}
        except Exception as e:
            logger.error(f"Erro SOAP cancelamento: {e}")
            raise NFSeBethaError(f"Erro SOAP cancelamento: {e}")

    def cancelar_nfse_assinado(self, numero_nfse: str, tpAmb: int = 1,
                                dak_empresa=None,
                                motivo: str = "Cancelamento solicitado",
                                chave_acesso: str = None,
                                protocolo_dps: str = None) -> dict:
        """Cancela NFS-e via RecepcionarEventoCancelamentoEnvio (DPS cloud)"""
        import re
        from datetime import datetime, timezone, timedelta

        logger.info(f"Cancelando NFS-e {numero_nfse} via evento...")
        cnpj = os.getenv('BETHA_CNPJ', '13133714000110')
        im = os.getenv('BETHA_INSCRICAO_MUNICIPAL', '')
        cmun = os.getenv('MUNICIPIO_CODIGO', '5003702')
        if not chave_acesso:
            raise NFSeBethaError("chave_acesso é obrigatória para cancelamento via evento")
        ns_e = "http://www.betha.com.br/e-nota-dps"
        # Formato ISO sem microssegundos para evitar JAXB parsing issues
        from database import SessionLocal
        from models import Empresa
        db_s = SessionLocal()
        emp = db_s.query(Empresa).first()
        offset_fuso = int(emp.fuso_horario if emp and emp.fuso_horario is not None else -4)
        db_s.close()

        FUSO_LOCAL = timezone(timedelta(hours=offset_fuso))
        now_local = datetime.now(FUSO_LOCAL).replace(tzinfo=None)
        sign = "+" if offset_fuso >= 0 else "-"
        abs_off = abs(offset_fuso)
        fuso_str = f"{sign}{abs_off:02d}:00"

        agora_s = now_local.strftime(f'%Y-%m-%dT%H:%M:%S{fuso_str}')
        evt_id = f"EVT{now_local.strftime('%Y%m%d%H%M%S%f')[:22]}"
        pre_id = f"PRE{now_local.strftime('%Y%m%d%H%M%S%f')[:22]}"
        n_dfe = f"{now_local.year}{str(numero_nfse).zfill(11)}"

        evento_xml = f'''<RecepcionarEventoCancelamentoEnvio xmlns="{ns_e}">
  <evento versao="1.0">
    <infEvento id="{evt_id}">
      <verAplic>1.0</verAplic>
      <ambGer>{tpAmb}</ambGer>
      <nSeqEvento>1</nSeqEvento>
      <dhProc>{agora_s}</dhProc>
      <nDFe>{n_dfe}</nDFe>
      <pedRegEvento versao="1.0">
        <infPedReg id="{pre_id}">
          <chNFSe>{chave_acesso}</chNFSe>
          <CNPJAutor>{cnpj}</CNPJAutor>
          <dhEvento>{agora_s}</dhEvento>
          <tpAmb>{tpAmb}</tpAmb>
          <verAplic>1.0</verAplic>
          <e101101>
            <xDesc>Cancelamento de NFS-e</xDesc>
            <cMotivo>1</cMotivo>
            <xMotivo>{motivo}</xMotivo>
          </e101101>
        </infPedReg>
      </pedRegEvento>
    </infEvento>
  </evento>
</RecepcionarEventoCancelamentoEnvio>'''

        xml_assinado = evento_xml
        logger.info(f"XML evento cancel:\n{xml_assinado[:500]}")

        # Envelopa SOAP
        soap_xml = f'''<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
   <soapenv:Header/>
   <soapenv:Body>
      {xml_assinado}
   </soapenv:Body>
</soapenv:Envelope>'''

        session = self._get_session()
        
        try:
            response = session.post(
                BETHA_NFSE_URL,
                data=soap_xml.encode('utf-8'),
                headers={'Content-Type': 'text/xml; charset=utf-8'},
                timeout=60
            )
            if response.status_code >= 400:
                body_err = response.text[:1500]
                logger.error(f"Erro HTTP {response.status_code} da Betha Cloud (enviar_dps): {body_err}")
                raise NFSeBethaError(f"Erro HTTP {response.status_code} da Betha Cloud: {body_err}")
            response.raise_for_status()
            text = response.text
            logger.info(f"Cancelamento evento response: {text[:2000]}")

            def _v(name):
                m = re.search(rf'<[^:>]*:?{name}[^>]*>([^<]*)</[^:>]*:?{name}[^>]*>', text)
                return m.group(1).strip() if m else None

            status = _v('status')
            protocolo = _v('protocolo')
            if status and status in ('Aguardando validação do ambiente nacional', 'sucesso'):
                return {'sucesso': True, 'protocolo': protocolo}
            if status and status.lower() != 'sucesso':
                msg = f"Status: {status}"
                if protocolo:
                    msg += f" (protocolo: {protocolo})"
                # Tenta consultar o protocolo para erro detalhado
                logger.info(f"Evento não processado, tentando CancelarDpsEnvio...")
                for param in [{'numero_nfse': numero_nfse},
                              {'protocolo': protocolo_dps}] if protocolo_dps else [{'numero_nfse': numero_nfse}]:
                    try:
                        fb = self.cancelar_dps(tpAmb=tpAmb, codigo_cancelamento="1", **param)
                        if fb.get('sucesso'):
                            return fb
                        logger.info(f"CancelarDpsEnvio {list(param.keys())[0]} falhou: {fb.get('erros')}")
                    except Exception as e:
                        logger.warning(f"CancelarDpsEnvio {list(param.keys())[0]} erro: {e}")
                return {'sucesso': False, 'erros': [{'mensagem': msg}]}
            if 'sucesso' in text.lower():
                return {'sucesso': True, 'protocolo': protocolo}
            msgs = re.findall(r'<[^:>]*:?mensagem[^>]*>([^<]+)</[^:>]*:?mensagem[^>]*>', text)
            msgs = list(dict.fromkeys(m.strip() for m in msgs if m.strip()))
            if msgs:
                return {'sucesso': False, 'erros': [{'mensagem': m} for m in msgs]}
            fault = re.search(r'<[^:>]*:?faultstring[^>]*>(.*?)</[^:>]*:?faultstring[^>]*>', text, re.DOTALL)
            if fault:
                return {'sucesso': False, 'erros': [{'mensagem': fault.group(1).strip()}]}
            logger.warning(f"Resposta cancelamento não reconhecida:\n{text[:3000]}")
            return {'sucesso': False, 'erros': [{'mensagem': f'Resposta não reconhecida: {text[:500]}'}]}
        except Exception as e:
            logger.error(f"Erro SOAP cancelamento evento: {e}")
            raise NFSeBethaError(f"Erro SOAP cancelamento evento: {e}")

    def _cancelar_abrasf(self, numero_nfse, motivo, tpAmb=1):
        """Fallback: ABRASF CancelarNfseEnvio com assinatura"""
        import re
        from lxml import etree
        from signxml import XMLSigner, methods
        from cryptography.hazmat.primitives.serialization import pkcs12, Encoding
        from datetime import datetime, timezone, timedelta

        logger.info(f"ABRASF cancel fallback NFS-e {numero_nfse}...")
        cnpj = os.getenv('BETHA_CNPJ', '13133714000110')
        im = os.getenv('BETHA_INSCRICAO_MUNICIPAL', '')
        cmun = os.getenv('MUNICIPIO_CODIGO', '5003702')
        ns = "http://www.betha.com.br/e-nota-dps"

        with open(self.cert_path, 'rb') as f:
            pfx_data = f.read()
        private_key, cert, _ = pkcs12.load_key_and_certificates(
            pfx_data,
            password=self.cert_password.encode() if self.cert_password else None
        )
        cert_pem = cert.public_bytes(Encoding.PEM).decode()

        cancel_xml = f'''<CancelarNfseEnvio xmlns="{ns}">
  <Pedido>
    <InfPedidoCancelamento Id="Cancelamento1">
      <IdentificacaoNfse>
        <Numero>{numero_nfse}</Numero>
        <CpfCnpj><Cnpj>{cnpj}</Cnpj></CpfCnpj>
        <InscricaoMunicipal>{im}</InscricaoMunicipal>
        <CodigoMunicipio>{cmun}</CodigoMunicipio>
      </IdentificacaoNfse>
      <CodigoCancelamento>1</CodigoCancelamento>
    </InfPedidoCancelamento>
  </Pedido>
</CancelarNfseEnvio>'''
        root = etree.fromstring(cancel_xml.encode())
        signer = XMLSigner(method=methods.enveloped, signature_algorithm='rsa-sha256',
                           digest_algorithm='sha256')
        signed_root = signer.sign(root, key=private_key, cert=cert_pem, reference_uri='#Cancelamento1')
        xml_assinado = etree.tostring(signed_root, encoding='unicode', pretty_print=True)

        soap_xml = f'''<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
   <soapenv:Header/>
   <soapenv:Body>{xml_assinado}</soapenv:Body>
</soapenv:Envelope>'''

        session = self._get_session()
        
        response = session.post(
            BETHA_NFSE_CANCEL_URL, data=soap_xml.encode('utf-8'),
            headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': 'CancelarNfseEnvio'},
            timeout=60
        )
        response.raise_for_status()
        text = response.text
        logger.info(f"ABRASF cancel response: {text[:1000]}")
        if 'sucesso' in text.lower() or re.search(r'CancelamentoHomologado', text):
            return {'sucesso': True}
        msgs = re.findall(r'<[^:>]*:?mensagem[^>]*>([^<]+)</[^:>]*:?mensagem[^>]*>', text)
        if msgs:
            return {'sucesso': False, 'erros': [{'mensagem': m} for m in msgs]}
        raise NFSeBethaError(f"ABRASF resposta não reconhecida: {text[:300]}")

def _limpar_codigo(valor) -> str:
    if not valor:
        return ""
    return "".join(c for c in str(valor) if c.isdigit())


def _esc(valor) -> str:
    """Escapa caracteres especiais XML (& < > " ') em campos de texto.
    Sem isso, um cliente com '&' no nome (ex.: 'X & Y LTDA') gera XML inválido
    e a Betha responde HTTP 400 com corpo vazio."""
    if not valor:
        return ""
    from xml.sax.saxutils import escape
    return escape(str(valor), {'"': '&quot;', "'": '&apos;'})


def gerar_dps_xml(pedido, db, tpAmb: int = 1, numero_nfse: int = None, serie: str = '1') -> str:
    """Gera XML DPS Nacional - formato ID 45 chars - filtra apenas serviços.
    O parâmetro `serie` (1 caractere) é variado nas retentativas para gerar um ID
    distinto mantendo o nDPS (número da nota) inalterado."""
    from models import Empresa
    empresa = db.query(Empresa).first()

    itens_servico = [i for i in pedido.itens if i.produto and i.produto.tipo == 'servico']

    cnpj_prest = _limpar_codigo(empresa.cnpj or '')
    cpf_cnpj_toma = _limpar_codigo(pedido.cliente.cpf_cnpj or '')

    cmun = os.getenv('MUNICIPIO_CODIGO', '5003702')

    cli = pedido.cliente
    cli_end = _esc((cli.endereco or '').strip())
    cli_bairro = _esc((cli.bairro or '').strip())
    cli_cep = _limpar_codigo(cli.cep or '')
    cli_cmun = _limpar_codigo(cli.codigo_ibge or '') or cmun
    cli_fone = _limpar_codigo(cli.celular or cli.telefone or '')
    cli_email = _esc((cli.email or '').strip())
    cli_im = _limpar_codigo(cli.inscricao_municipal or '')

    toma_end = f'''<end>
         <endNac>
            <cMun>{cli_cmun}</cMun>
            <CEP>{cli_cep}</CEP>
         </endNac>
         <xLgr>{cli_end}</xLgr>
         <nro>SN</nro>
         <xBairro>{cli_bairro}</xBairro>
      </end>'''

    if len(cpf_cnpj_toma) == 11:
        toma_doc = f'<CPF>{cpf_cnpj_toma}</CPF>'
    else:
        toma_doc = f'<CNPJ>{cpf_cnpj_toma}</CNPJ>'
        if cli_im:
            toma_doc += f'\n         <IM>{cli_im}</IM>'

    serie = (serie or '1')[:1]
    ndps_num = numero_nfse if numero_nfse is not None else pedido.id
    ndps = f"{ndps_num:015d}"

    service = BethaNfseService()
    id_dps = service.gerar_id_dps(cmun, cnpj_prest, serie, ndps)

    total_vlr = sum(float(i.total or 0) for i in itens_servico)
    if total_vlr == 0:
        total_vlr = float(pedido.total or 0)

    offset_fuso = int(empresa.fuso_horario if empresa and empresa.fuso_horario is not None else -4)
    FUSO_LOCAL = timezone(timedelta(hours=offset_fuso))
    sign = "+" if offset_fuso >= 0 else "-"
    abs_off = abs(offset_fuso)
    fuso_str = f"{sign}{abs_off:02d}:00"

    raw = pedido.data
    if raw:
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        data_emissao = raw.astimezone(FUSO_LOCAL)
    else:
        data_emissao = datetime.now(FUSO_LOCAL)

    cod_serv = "010101"
    desc_serv = "Servicos"
    cod_nbs = ""
    discriminacao = ""

    for i, item in enumerate(itens_servico, 1):
        prod = item.produto
        desc = item.descricao or (prod.nome if prod else "Servico")
        qtd = float(item.quantidade or 1)
        if i == 1:
            if prod and prod.codigo_lc116:
                cod_serv = (_limpar_codigo(prod.codigo_lc116)).ljust(6, '0')
            desc_serv = desc
            if prod and prod.codigo_tributacao_municipal:
                cod_nbs = _limpar_codigo(prod.codigo_tributacao_municipal)
        if discriminacao:
            discriminacao += " | "
        discriminacao += f"{i}. {desc} (qtd: {qtd})"

    if not discriminacao:
        discriminacao = desc_serv

    aliquota = float(empresa.aliquota_iss or 2.0)
    iss_retido = getattr(pedido.cliente, 'iss_retido', False) or False
    tp_ret = 2 if iss_retido else 1

    ali_fed = float(empresa.aliquota_federal or 0.0)
    ali_est = float(empresa.aliquota_estadual or 0.0)
    ali_mun = float(empresa.aliquota_municipal or 0.0)
    p_tot_trib = ali_fed + ali_est + ali_mun

    desc_serv = _esc(discriminacao)

    if ali_fed > 0 or ali_est > 0 or ali_mun > 0:
        tot_trib = f"""         <totTrib>
               <pTotTrib>
                  <pTotTribFed>{ali_fed:.2f}</pTotTribFed>
                  <pTotTribEst>{ali_est:.2f}</pTotTribEst>
                  <pTotTribMun>{ali_mun:.2f}</pTotTribMun>
               </pTotTrib>
            </totTrib>"""
    else:
        tot_trib = """         <totTrib>
               <indTotTrib>0</indTotTrib>
            </totTrib>"""

    prest_fone_val = _limpar_codigo(empresa.celular or empresa.telefone or '')
    prest_fone_tag = f'<fone>{prest_fone_val}</fone>' if prest_fone_val else ''
    prest_email_val = _esc((empresa.email or '').strip())
    prest_email_tag = f'<email>{prest_email_val}</email>' if prest_email_val else ''
    tom_fone_tag = f'<fone>{cli_fone}</fone>' if cli_fone else ''
    tom_email_tag = f'<email>{cli_email}</email>' if cli_email else ''

    return f'''<DPS xmlns="http://www.betha.com.br/e-nota-dps" versao="1.01">
   <infDPS id="{id_dps}">
      <tpAmb>{tpAmb}</tpAmb>
        <dhEmi>{data_emissao.strftime(f'%Y-%m-%dT%H:%M:%S{fuso_str}')}</dhEmi>
       <verAplic>fly_WS_1.1.0</verAplic>
       <serie>{serie}</serie>
       <nDPS>{ndps}</nDPS>
       <dCompet>{data_emissao.strftime('%Y-%m-%d')}</dCompet>
      <tpEmit>1</tpEmit>
      <cLocEmi>{cmun}</cLocEmi>
      <prest>
         <CNPJ>{cnpj_prest}</CNPJ>
         {prest_fone_tag}
         {prest_email_tag}
         <regTrib>
            <opSimpNac>1</opSimpNac>
            <regEspTrib>0</regEspTrib>
         </regTrib>
      </prest>
       <toma>
          {toma_doc}
          <xNome>{_esc(cli.nome or '')}</xNome>
          {toma_end}
          {tom_fone_tag}
          {tom_email_tag}
       </toma>
       <serv>
         <locPrest>
            <cLocPrestacao>{cmun}</cLocPrestacao>
         </locPrest>
         <cServ>
            <cTribNac>{cod_serv}</cTribNac>
            <xDescServ>{desc_serv}</xDescServ>
            <cNBS>{cod_nbs or '010101'}</cNBS>
         </cServ>
      </serv>
      <valores>
         <vServPrest>
            <vServ>{total_vlr:.2f}</vServ>
         </vServPrest>
         <trib>
            <tribMun>
               <tribISSQN>1</tribISSQN>
               <pAliq>{aliquota:.2f}</pAliq>
               <tpRetISSQN>{tp_ret}</tpRetISSQN>
            </tribMun>
            {tot_trib}
         </trib>
      </valores>
   </infDPS>
</DPS>'''

def gerar_dps_xml_nfse(nfse, db, tpAmb: int = 1, numero_nfse: int = None, serie: str = '1') -> str:
    """Gera XML DPS Nacional a partir de uma NFSe já registrada.
    O parâmetro `serie` (1 caractere) é variado nas retentativas para gerar um ID
    distinto mantendo o nDPS (número da nota) inalterado."""
    from models import Empresa
    empresa = db.query(Empresa).first()

    itens = nfse.itens or []
    cnpj_prest = _limpar_codigo(empresa.cnpj or '')
    cpf_cnpj_toma = _limpar_codigo(nfse.cliente.cpf_cnpj or '') if nfse.cliente else ''
    cmun = os.getenv('MUNICIPIO_CODIGO', '5003702')
    cli = nfse.cliente

    cli_end = _esc((cli.endereco or '').strip()) if cli else ''
    cli_bairro = _esc((cli.bairro or '').strip()) if cli else ''
    cli_cep = _limpar_codigo(cli.cep or '') if cli else ''
    cli_cmun = _limpar_codigo(cli.codigo_ibge or '') or cmun if cli else cmun
    cli_fone = _limpar_codigo(cli.celular or cli.telefone or '') if cli else ''
    cli_email = _esc((cli.email or '').strip()) if cli else ''
    cli_im = _limpar_codigo(cli.inscricao_municipal or '') if cli else ''
    cli_nome = _esc(cli.nome or '') if cli else ''

    toma_end = f'''<end>
         <endNac>
            <cMun>{cli_cmun}</cMun>
            <CEP>{cli_cep}</CEP>
         </endNac>
         <xLgr>{cli_end}</xLgr>
         <nro>SN</nro>
         <xBairro>{cli_bairro}</xBairro>
      </end>'''

    if len(cpf_cnpj_toma) == 11:
        toma_doc = f'<CPF>{cpf_cnpj_toma}</CPF>'
    else:
        toma_doc = f'<CNPJ>{cpf_cnpj_toma}</CNPJ>'
        if cli_im:
            toma_doc += f'\n         <IM>{cli_im}</IM>'

    serie = (serie or '1')[:1]
    ndps_num = numero_nfse if numero_nfse is not None else (int(nfse.numero) if nfse.numero and nfse.numero.isdigit() else nfse.id)
    ndps = f"{ndps_num:015d}"

    service = BethaNfseService()
    id_dps = service.gerar_id_dps(cmun, cnpj_prest, serie, ndps)

    total_vlr = sum(float(i.valor_total or 0) for i in itens)
    if total_vlr == 0:
        total_vlr = float(nfse.valor_total or 0)

    offset_fuso = int(empresa.fuso_horario if empresa and empresa.fuso_horario is not None else -4)
    FUSO_LOCAL = timezone(timedelta(hours=offset_fuso))
    sign = "+" if offset_fuso >= 0 else "-"
    abs_off = abs(offset_fuso)
    fuso_str = f"{sign}{abs_off:02d}:00"

    raw = nfse.data_emissao
    if raw:
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        data_emissao = raw.astimezone(FUSO_LOCAL)
    else:
        data_emissao = datetime.now(FUSO_LOCAL)

    cod_serv = "010101"
    desc_serv = "Servicos"
    cod_nbs = ""
    discriminacao = ""

    for i, item in enumerate(itens, 1):
        desc = item.descricao or "Servico"
        qtd = float(item.quantidade or 1)
        if i == 1:
            cod_serv = (_limpar_codigo(item.codigo_servico or '')).ljust(6, '0') or cod_serv
            desc_serv = desc
            cod_nbs = _limpar_codigo(item.tributacao_municipal or '') or cod_nbs
        if discriminacao:
            discriminacao += " | "
        discriminacao += f"{i}. {desc} (qtd: {qtd})"

    if not discriminacao:
        discriminacao = desc_serv

    aliquota = float(empresa.aliquota_iss or 2.0)
    # Considera a flag da NFSe OU do cliente (cliente pode ter sido marcado como
    # ISS retido depois da criação da NFSe)
    iss_retido = bool(getattr(nfse, 'iss_retido', False) or (cli and getattr(cli, 'iss_retido', False)))
    tp_ret = 2 if iss_retido else 1

    ali_fed = float(nfse.aliquota_federal if nfse.aliquota_federal is not None else (empresa.aliquota_federal or 0.0))
    ali_est = float(nfse.aliquota_estadual if nfse.aliquota_estadual is not None else (empresa.aliquota_estadual or 0.0))
    ali_mun = float(nfse.aliquota_municipal if nfse.aliquota_municipal is not None else (empresa.aliquota_municipal or 0.0))
    p_tot_trib = ali_fed + ali_est + ali_mun

    observacoes = getattr(nfse, 'observacoes', '') or ''
    desc_serv = discriminacao
    if observacoes:
        desc_serv += f" | {observacoes}"
    desc_serv = _esc(desc_serv)

    if ali_fed > 0 or ali_est > 0 or ali_mun > 0:
        tot_trib = f"""         <totTrib>
               <pTotTrib>
                  <pTotTribFed>{ali_fed:.2f}</pTotTribFed>
                  <pTotTribEst>{ali_est:.2f}</pTotTribEst>
                  <pTotTribMun>{ali_mun:.2f}</pTotTribMun>
               </pTotTrib>
            </totTrib>"""
    else:
        tot_trib = """         <totTrib>
               <indTotTrib>0</indTotTrib>
            </totTrib>"""

    prest_fone_val = _limpar_codigo(empresa.celular or empresa.telefone or '')
    prest_fone_tag = f'<fone>{prest_fone_val}</fone>' if prest_fone_val else ''
    prest_email_val = _esc((empresa.email or '').strip())
    prest_email_tag = f'<email>{prest_email_val}</email>' if prest_email_val else ''
    tom_fone_tag = f'<fone>{cli_fone}</fone>' if cli_fone else ''
    tom_email_tag = f'<email>{cli_email}</email>' if cli_email else ''

    return f'''<DPS xmlns="http://www.betha.com.br/e-nota-dps" versao="1.01">
   <infDPS id="{id_dps}">
      <tpAmb>{tpAmb}</tpAmb>
        <dhEmi>{data_emissao.strftime(f'%Y-%m-%dT%H:%M:%S{fuso_str}')}</dhEmi>
       <verAplic>fly_WS_1.1.0</verAplic>
       <serie>{serie}</serie>
       <nDPS>{ndps}</nDPS>
       <dCompet>{data_emissao.strftime('%Y-%m-%d')}</dCompet>
      <tpEmit>1</tpEmit>
      <cLocEmi>{cmun}</cLocEmi>
      <prest>
         <CNPJ>{cnpj_prest}</CNPJ>
         {prest_fone_tag}
         {prest_email_tag}
         <regTrib>
            <opSimpNac>1</opSimpNac>
            <regEspTrib>0</regEspTrib>
         </regTrib>
      </prest>
       <toma>
          {toma_doc}
          <xNome>{cli_nome}</xNome>
          {toma_end}
          {tom_fone_tag}
          {tom_email_tag}
       </toma>
       <serv>
         <locPrest>
            <cLocPrestacao>{cmun}</cLocPrestacao>
         </locPrest>
         <cServ>
            <cTribNac>{cod_serv}</cTribNac>
            <xDescServ>{desc_serv}</xDescServ>
            <cNBS>{cod_nbs or '010101'}</cNBS>
         </cServ>
      </serv>
      <valores>
         <vServPrest>
            <vServ>{total_vlr:.2f}</vServ>
                    </vServPrest>
         <trib>
            <tribMun>
               <tribISSQN>1</tribISSQN>
               <pAliq>{aliquota:.2f}</pAliq>
               <tpRetISSQN>{tp_ret}</tpRetISSQN>
            </tribMun>
            {tot_trib}
         </trib>
      </valores>
   </infDPS>
</DPS>'''


def emitir_rascunho(nfse, db, tpAmb: int = 1, attempt: int = 0) -> dict:
    import time
    try:
        service = BethaNfseService()
        numero = int(nfse.numero) if nfse.numero and nfse.numero.isdigit() else None

        def _send_with_serie(attempt: int):
            # Varia a série (1 dígito) para gerar um ID DPS distinto nas retentativas,
            # mantendo o nDPS (número da nota) inalterado. Evita E001 (ID com 48 chars)
            # e E050 (DPS duplicada) sem pular o número da nota.
            serie = str((1 + attempt) % 10)
            dps_xml = gerar_dps_xml_nfse(nfse, db, tpAmb, numero, serie=serie)
            return service.enviar_dps(dps_xml, tpAmb), dps_xml

        # Primeira tentativa
        resultado, dps_xml = _send_with_serie(attempt)

        retry_iss_retido = False

        # Se erro de ISS retido, corrige e reenvia com série diferente
        if resultado.get('erros') and _erro_iss_retido(resultado['erros']):
            attempt += 1
            logger.info("Betha rejeitou por ISS retido — corrigindo e reenviando...")
            nfse.iss_retido = True
            if nfse.cliente:
                nfse.cliente.iss_retido = True
            db.commit()
            resultado, dps_xml = _send_with_serie(attempt)
            retry_iss_retido = True

        # Se DPS duplicada (E050), reenvia com séries diferentes mantendo mesmo nDPS.
        # Loop até 10 séries (0-9): tentativas anteriores podem já ter consumido
        # várias séries (ex.: série 1 na emissão original, série 2 num retry antigo),
        # e a Betha devolve E050 para cada ID já recepcionado.
        while resultado.get('erros') and _erro_dps_duplicada(resultado['erros']) and attempt < 9:
            attempt += 1
            logger.info(f"DPS duplicada — reenviando com série variada (attempt {attempt})...")
            resultado, dps_xml = _send_with_serie(attempt)

        protocolo = resultado.get('protocolo')
        erros = resultado.get('erros', [])
        data_original = nfse.data_emissao
        if erros:
            return {
                'status_processamento': 'erro',
                'protocolo': protocolo,
                'numero': None,
                'codigo_verificacao': None,
                'xml': dps_xml,
                'data_emissao': data_original,
                'erros': erros,
                'retry_iss_retido': retry_iss_retido,
            }
        for tentativa in range(6):
            time.sleep(5)
            status = service.consultar_status(protocolo, tpAmb)
            st = status.get('status', '')
            if st == 'Processado com sucesso':
                return {
                    'status_processamento': 'sucesso',
                    'protocolo': protocolo,
                    'numero': status.get('numero_nfse'),
                    'codigo_verificacao': status.get('chave_acesso'),
                    'xml': dps_xml,
                    'xml_documento': status.get('xml_documento'),
                    'data_emissao': data_original,
                    'erros': [],
                    'retry_iss_retido': retry_iss_retido,
                }
            elif st == 'Processado com erro':
                erros_status = status.get('erros', [])
                # Município exige ISS retido pelo tomador (erro pós-processamento).
                # Reenvia com série variada (novo ID DPS) mesmo se a flag já estiver
                # ativa: quando o ID DPS repete um envio anterior, a Betha devolve o
                # resultado antigo armazenado em vez de reprocessar o novo XML.
                if _erro_iss_retido(erros_status) and attempt < 3:
                    if not getattr(nfse, 'iss_retido', False):
                        nfse.iss_retido = True
                        if nfse.cliente:
                            nfse.cliente.iss_retido = True
                        db.commit()
                    logger.info(f"Prefeitura rejeitou por ISS retido (pós-processamento) — reenviando com novo ID DPS (attempt {attempt + 1})...")
                    novo = emitir_rascunho(nfse, db, tpAmb, attempt + 1)
                    novo['retry_iss_retido'] = True
                    return novo
                return {
                    'status_processamento': 'erro',
                    'protocolo': protocolo,
                    'numero': None,
                    'codigo_verificacao': None,
                    'xml': dps_xml,
                    'data_emissao': data_original,
                    'erros': erros_status,
                    'retry_iss_retido': retry_iss_retido,
                }
        return {
            'status_processamento': 'processando',
            'protocolo': protocolo,
            'numero': None,
            'codigo_verificacao': None,
            'xml': None,
            'data_emissao': data_original,
            'erros': [{'mensagem': 'NFSe enviada, aguardando processamento na prefeitura'}],
            'retry_iss_retido': retry_iss_retido,
        }
    except NFSeBethaError:
        raise
    except Exception as e:
        raise NFSeBethaError(f"Erro inesperado: {e}")


def sincronizar_nfse(protocolo: str, tpAmb: int = 1, numero_nfse: str = None) -> dict:
    try:
        service = BethaNfseService()
        status = service.consultar_status(protocolo, tpAmb)
        st = status.get('status', '')
        sit_raw = None
        adn_xml = None

        # Cancelamento detectado via Betha (SOAP consultar_status) — fonte primária,
        # independente do SEFIN (que está indisponível/403).
        sit_nfse = (status.get('situacao_nfse') or '').strip().lower()
        if sit_nfse in ('2', 'cancelada', 'cancelado') or 'cancel' in sit_nfse:
            return {
                'status_processamento': 'cancelada',
                'numero': numero_nfse,
                'codigo_verificacao': status.get('chave_acesso'),
                'erros': [],
            }

        if numero_nfse:
            sit_resp = service.consultar_situacao_nfse(numero_nfse, status.get('chave_acesso'))
            eventos = sit_resp.get('eventos')
            ev_debug = sit_resp.get('eventos_debug') or ''
            sit_raw = f"sit={sit_resp.get('situacao')} eventos:{ev_debug[:400]}"
            sit = (sit_resp.get('situacao') or '').lower()
            codigo = sit_resp.get('codigo', '')
            adn_xml = sit_resp.get('xml')
            chave_adn = sit_resp.get('chaveAcesso') or status.get('chave_acesso')
            if codigo == '2' or 'cancelada' in sit or 'cancelado' in sit:
                return {
                    'status_processamento': 'cancelada',
                    'numero': numero_nfse,
                    'codigo_verificacao': chave_adn,
                    'erros': [],
                }
        if st == 'Processado com sucesso':
            return {
                'status_processamento': 'sucesso',
                'numero': status.get('numero_nfse'),
                'codigo_verificacao': status.get('chave_acesso'),
                'xml_documento': adn_xml or status.get('xml_documento'),
                'url_danfse': status.get('url_danfse'),
                'erros': [],
                '_debug_raw': sit_raw,
                '_adn_xml': adn_xml,
            }
        elif st == 'Processado com erro':
            return {
                'status_processamento': 'erro',
                'numero': None,
                'codigo_verificacao': None,
                'erros': status.get('erros', []),
            }
        else:
            return {
                'status_processamento': 'processando',
                'numero': None,
                'codigo_verificacao': None,
                'erros': [{'mensagem': 'Ainda em processamento na prefeitura'}],
            }
    except NFSeBethaError:
        raise
    except Exception as e:
        raise NFSeBethaError(f"Erro ao sincronizar: {e}")


def _erro_dps_duplicada(erros: list) -> bool:
    """Detecta E050 (DPS já recepcionada anteriormente)."""
    if not erros:
        return False
    for e in erros:
        if e.get('codigo') == 'E050':
            return True
        msg = (e.get('mensagem') or '').lower()
        if 'recepcionada' in msg or ('dps' in msg and 'recepcionad' in msg):
            return True
    return False


def _erro_iss_retido(erros: list) -> bool:
    if not erros:
        return False
    msg = ' '.join((e.get('mensagem') or '').lower() for e in erros)
    # Mensagem nova do município (não contém "iss"):
    # "Tomador nomeado 'Substituto Tributário' pelo Município. Para correção,
    #  altere a situação tributária para 'R - Retido pelo Tomador'."
    if 'substituto tribut' in msg:
        return True
    if 'retido pelo tomador' in msg:
        return True
    return 'iss' in msg and any(kw in msg for kw in ('retenção', 'retido', 'retencao', 'tomador'))


def emitir_completa(pedido, db, tpAmb: int = 1, numero_nfse: int = None, attempt: int = 0) -> dict:
    import time
    try:
        service = BethaNfseService()

        def _send_with_serie(attempt: int):
            # Varia a série (1 dígito) para gerar um ID DPS distinto nas retentativas,
            # mantendo o nDPS (número da nota) inalterado. Evita E001 (ID com 48 chars)
            # e E050 (DPS duplicada) sem pular o número da nota.
            serie = str((1 + attempt) % 10)
            dps_xml = gerar_dps_xml(pedido, db, tpAmb, numero_nfse, serie=serie)
            return service.enviar_dps(dps_xml, tpAmb), dps_xml

        resultado, dps_xml = _send_with_serie(attempt)

        retry_iss_retido = False
        if resultado.get('erros') and _erro_iss_retido(resultado['erros']):
            attempt += 1
            logger.info("Betha rejeitou por ISS retido — corrigindo e reenviando...")
            pedido.cliente.iss_retido = True
            db.commit()
            resultado, dps_xml = _send_with_serie(attempt)
            retry_iss_retido = True

        # Se DPS duplicada (E050), reenvia com séries diferentes mantendo mesmo nDPS.
        # Loop até 10 séries (0-9): tentativas anteriores podem já ter consumido
        # várias séries, e a Betha devolve E050 para cada ID já recepcionado.
        while resultado.get('erros') and _erro_dps_duplicada(resultado['erros']) and attempt < 9:
            attempt += 1
            logger.info(f"DPS duplicada — reenviando com série variada (attempt {attempt})...")
            resultado, dps_xml = _send_with_serie(attempt)

        protocolo = resultado.get('protocolo')
        data_original = pedido.data
        erros = resultado.get('erros', [])
        if erros:
            return {
                'protocolo': protocolo,
                'numero': None,
                'codigo_verificacao': None,
                'xml': dps_xml,
                'data_emissao': data_original,
                'erros': erros,
                'retry_iss_retido': retry_iss_retido,
            }
        # Aguarda processamento nacional (consulta com retry até 120s)
        for tentativa in range(24):
            time.sleep(5)
            status = service.consultar_status(protocolo, tpAmb)
            st = status.get('status', '')
            if st == 'Processado com sucesso':
                return {
                    'protocolo': protocolo,
                    'numero': status.get('numero_nfse'),
                    'codigo_verificacao': status.get('chave_acesso'),
                    'xml': dps_xml,
                    'xml_documento': status.get('xml_documento'),
                    'data_emissao': data_original,
                    'erros': [],
                    'retry_iss_retido': retry_iss_retido,
                }
            elif st == 'Processado com erro':
                erros_status = status.get('erros', [])
                # Município exige ISS retido pelo tomador (erro pós-processamento).
                # Reenvia com série variada (novo ID DPS) mesmo se a flag já estiver
                # ativa: quando o ID DPS repete um envio anterior, a Betha devolve o
                # resultado antigo armazenado em vez de reprocessar o novo XML.
                if _erro_iss_retido(erros_status) and attempt < 3:
                    if not getattr(pedido.cliente, 'iss_retido', False):
                        pedido.cliente.iss_retido = True
                        db.commit()
                    logger.info(f"Prefeitura rejeitou por ISS retido (pós-processamento) — reenviando com novo ID DPS (attempt {attempt + 1})...")
                    novo = emitir_completa(pedido, db, tpAmb, numero_nfse, attempt + 1)
                    novo['retry_iss_retido'] = True
                    return novo
                return {
                    'protocolo': protocolo,
                    'numero': None,
                    'codigo_verificacao': None,
                    'xml': dps_xml,
                    'data_emissao': data_original,
                    'erros': erros_status,
                    'retry_iss_retido': retry_iss_retido,
                }
        return {
            'protocolo': protocolo,
            'numero': None,
            'codigo_verificacao': None,
            'xml': dps_xml,
            'data_emissao': data_original,
            'erros': [{'mensagem': 'Tempo limite excedido aguardando processamento'}],
            'retry_iss_retido': retry_iss_retido,
        }
    except NFSeBethaError:
        raise
    except Exception as e:
        raise NFSeBethaError(f"Erro inesperado: {e}")