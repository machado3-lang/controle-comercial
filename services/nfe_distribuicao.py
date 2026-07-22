import os, re, base64, gzip, logging, time
from typing import Optional
from requests import Session
from models import Empresa, NFeDistribuida
from app.core.config import settings

logger = logging.getLogger(__name__)

NFE_DIST_URL_PROD = settings.NFE_DIST_URL_PROD
NFE_DIST_URL_HOMOL = settings.NFE_DIST_URL_HOMOL


class NFeDistribuicaoError(Exception):
    pass


class NFeDistribuicaoService:
    def __init__(self, empresa: Empresa = None, db=None):
        self.empresa = empresa
        self.db = db
        self.cert_path = None
        self.cert_password = None
        self.tpAmb = 1
        self.url = NFE_DIST_URL_PROD
        self.ultimo_cstat = ""
        self.ultimo_motivo = ""
        if empresa:
            self._load_cert(empresa)
            self.tpAmb = int(empresa.notaas_ambiente) if empresa.notaas_ambiente else 1
            if self.tpAmb == 2:
                self.url = NFE_DIST_URL_HOMOL

    def _load_cert(self, empresa: Empresa):
        import tempfile
        # Try secure cert_store first
        if getattr(empresa, 'cert_id', None):
            from services.cert_store import load_certificate
            pfx_data = load_certificate("empresa", empresa.cert_id)
            if pfx_data:
                tmp = os.path.join(tempfile.gettempdir(), 'certificado_nfe.pfx')
                with open(tmp, 'wb') as f:
                    f.write(pfx_data)
                self.cert_path = tmp
                self.cert_password = empresa.cert_password or os.getenv('CERT_PASSWORD', '')
                return
        # Fallback: base64 legacy
        if empresa.cert_base64:
            pfx = base64.b64decode(empresa.cert_base64)
            tmp = os.path.join(tempfile.gettempdir(), 'certificado_nfe.pfx')
            with open(tmp, 'wb') as f:
                f.write(pfx)
            self.cert_path = tmp
            self.cert_password = empresa.cert_password or ''
        elif empresa.cert_path:
            self.cert_path = empresa.cert_path
            self.cert_password = empresa.cert_password or ''
        else:
            self.cert_path = os.getenv('CERT_PATH', './certs/certificado.pfx')
            self.cert_password = os.getenv('CERT_PASSWORD')

    def _get_pem_combined(self) -> str:
        from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
        import tempfile
        with open(self.cert_path, 'rb') as f:
            pfx_data = f.read()
        private_key, cert, _ = pkcs12.load_key_and_certificates(
            pfx_data, password=self.cert_password.encode() if self.cert_password else None
        )
        combined = private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=NoEncryption()
        ) + cert.public_bytes(Encoding.PEM)
        tmp = os.path.join(tempfile.gettempdir(), 'cert_combined_nfe.pem')
        with open(tmp, 'wb') as f:
            f.write(combined)
        return tmp

    def _get_session(self) -> Session:
        session = Session()
        if self.cert_path and os.path.exists(self.cert_path):
            pem = self._get_pem_combined()
            session.cert = pem
        return session

    def _build_soap_envelope(self, cnpj: str, ultNSU: str = "000000000000000") -> str:
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
   <soap:Header/>
   <soap:Body>
      <nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">
         <nfeDadosMsg>
            <distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00">
               <tpAmb>{self.tpAmb}</tpAmb>
               <cUFAutor>50</cUFAutor>
               <CNPJ>{cnpj}</CNPJ>
               <distNSU>
                  <ultNSU>{ultNSU}</ultNSU>
               </distNSU>
            </distDFeInt>
         </nfeDadosMsg>
      </nfeDistDFeInteresse>
   </soap:Body>
</soap:Envelope>'''

    def _build_soap_envelope_chave(self, cnpj: str, chave_acesso: str) -> str:
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
   <soap:Header/>
   <soap:Body>
      <nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">
         <nfeDadosMsg>
            <distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
               <tpAmb>{self.tpAmb}</tpAmb>
               <cUFAutor>50</cUFAutor>
               <CNPJ>{cnpj}</CNPJ>
               <consChNFe>
                  <chNFe>{chave_acesso}</chNFe>
               </consChNFe>
            </distDFeInt>
         </nfeDadosMsg>
      </nfeDistDFeInteresse>
   </soap:Body>
</soap:Envelope>'''

    def consultar_por_chave(self, cnpj: str, chave_acesso: str) -> dict:
        """Consulta uma NFe específica pela chave de acesso via SEFAZ (consChNFe)"""
        session = self._get_session()
        cnpj_clean = re.sub(r'\D', '', cnpj)
        chave_clean = re.sub(r'\D', '', chave_acesso)
        if len(chave_clean) != 44:
            raise NFeDistribuicaoError("Chave de acesso deve ter 44 dígitos")

        envelope = self._build_soap_envelope_chave(cnpj_clean, chave_clean)
        headers = {'Content-Type': 'text/xml;charset=UTF-8',
                   'SOAPAction': 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse'}
        r = session.post(self.url, data=envelope.encode('utf-8'), headers=headers, timeout=60)
        if r.status_code != 200:
            raise NFeDistribuicaoError(f"SEFAZ HTTP {r.status_code}: {r.text[:300]}")

        xml_body = r.text
        docs, _, cStat, xMotivo = self._parse_response(xml_body)
        if not docs:
            m = re.search(r'<xMotivo>([^<]+)</', xml_body)
            motivo = m.group(1) if m else 'NFe não encontrada'
            raise NFeDistribuicaoError(f"{motivo} (cStat={cStat})")

        doc = docs[0]
        if not doc['xml']:
            raise NFeDistribuicaoError("XML da NFe não disponível na consulta")

        chave, numero, dh_emi, valor, emitente_nome, emitente_cnpj, destinatario_nome, destinatario_cnpj = self._extract_nfe_info(doc['xml'])
        resultado = {
            'chaveAcesso': chave,
            'numero': numero,
            'dhEmi': dh_emi,
            'valor': valor,
            'emitente_nome': emitente_nome,
            'emitente_cnpj': emitente_cnpj,
            'destinatario_nome': destinatario_nome,
            'destinatario_cnpj': destinatario_cnpj,
            'NSU': doc['NSU'],
            'schema': doc['schema'],
            'xml': doc['xml'],
        }
        self._salvar_local(chave, numero, dh_emi, valor, emitente_nome, emitente_cnpj,
                           destinatario_nome, destinatario_cnpj, doc['NSU'], doc['schema'], doc['xml'])
        return resultado

    def importar_xml(self, xml_str: str) -> dict:
        """Importa uma NFe a partir de um XML já existente, extrai os dados e salva no banco local."""
        chave, numero, dh_emi, valor, emitente_nome, emitente_cnpj, destinatario_nome, destinatario_cnpj = self._extract_nfe_info(xml_str)
        if not chave:
            raise NFeDistribuicaoError("Não foi possível extrair a chave de acesso do XML")
        resultado = {
            'chaveAcesso': chave,
            'numero': numero,
            'dhEmi': dh_emi,
            'valor': valor,
            'emitente_nome': emitente_nome,
            'emitente_cnpj': emitente_cnpj,
            'destinatario_nome': destinatario_nome,
            'destinatario_cnpj': destinatario_cnpj,
            'NSU': '',
            'schema': 'NFe',
            'xml': xml_str,
        }
        self._salvar_local(chave, numero, dh_emi, valor, emitente_nome, emitente_cnpj,
                           destinatario_nome, destinatario_cnpj, '', 'NFe', xml_str)
        return resultado

    def _parse_response(self, xml: str) -> tuple[list[dict], str, str, str]:
        docs = []
        ultNSU = "000000000000000"
        cStat = ""
        xMotivo = ""
        for m in re.finditer(r'<docZip[^>]*NSU="(\d+)"[^>]*schema="([^"]+)"[^>]*>(.*?)</docZip>', xml, re.DOTALL):
            nsu = m.group(1)
            schema = m.group(2)
            raw = m.group(3).strip()
            try:
                content = gzip.decompress(base64.b64decode(raw)).decode('utf-8')
            except Exception:
                try:
                    content = base64.b64decode(raw).decode('utf-8')
                except Exception:
                    content = None
            docs.append({'NSU': nsu, 'schema': schema, 'xml': content})
        m = re.search(r'<ultNSU>(\d+)</', xml)
        if m: ultNSU = m.group(1)
        m = re.search(r'<cStat>(\d+)</', xml)
        if m: cStat = m.group(1)
        m = re.search(r'<xMotivo>([^<]+)</', xml)
        if m: xMotivo = m.group(1)
        return docs, ultNSU, cStat, xMotivo

    def listar_nfe(self, cnpj: str, max_paginas: int = 20) -> list[dict]:
        session = self._get_session()
        resultados = []
        pagina = 0
        cnpj_clean = re.sub(r'\D', '', cnpj)

        # Usa ultNSU persistido ou começa do zero
        ultNSU = self.empresa.nfe_ultnsu or "000000000000000"
        logger.info(f"Listando NFe da SEFAZ para CNPJ {cnpj_clean} (ultNSU={ultNSU})...")

        while pagina < max_paginas:
            try:
                envelope = self._build_soap_envelope(cnpj_clean, ultNSU)
                headers = {'Content-Type': 'text/xml;charset=UTF-8',
                           'SOAPAction': 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse'}
                r = session.post(self.url, data=envelope.encode('utf-8'), headers=headers, timeout=60)
                if r.status_code != 200:
                    logger.warning(f"SEFAZ NFe HTTP {r.status_code}: {r.text[:500]}")
                    break
                xml_body = r.text
                docs, novo_ultNSU, cStat, xMotivo = self._parse_response(xml_body)
                if cStat:
                    self.ultimo_cstat = cStat
                    self.ultimo_motivo = xMotivo
                logger.info(f"SEFAZ página {pagina+1}: {len(docs)} docs, cStat={cStat}, xMotivo={xMotivo}, ultNSU={novo_ultNSU}")

                if cStat in ('137',):
                    break
                if cStat in ('656',):
                    logger.warning(f"SEFAZ rate limit: {cStat}")
                    break

                for doc in docs:
                    if not doc['xml']:
                        continue
                    if 'procEvento' in doc.get('schema', '') or 'Evento' in doc.get('schema', ''):
                        continue
                    chave, numero, dh_emi, valor, emitente_nome, emitente_cnpj, destinatario_nome, destinatario_cnpj = self._extract_nfe_info(doc['xml'])
                    resultados.append({
                        'chaveAcesso': chave,
                        'numero': numero,
                        'dhEmi': dh_emi,
                        'valor': valor,
                        'emitente_nome': emitente_nome,
                        'emitente_cnpj': emitente_cnpj,
                        'destinatario_nome': destinatario_nome,
                        'destinatario_cnpj': destinatario_cnpj,
                        'NSU': doc['NSU'],
                        'schema': doc['schema'],
                        'xml': doc['xml'],
                    })
                    # Salva no banco local
                    self._salvar_local(chave, numero, dh_emi, valor, emitente_nome, emitente_cnpj,
                                       destinatario_nome, destinatario_cnpj, doc['NSU'], doc['schema'], doc['xml'])

                if novo_ultNSU == ultNSU:
                    break
                ultNSU = novo_ultNSU
                # Persiste ultNSU a cada página
                if self.db and self.empresa:
                    self.empresa.nfe_ultnsu = ultNSU
                    self.db.commit()
                pagina += 1
                time.sleep(3)

            except Exception as e:
                logger.error(f"Erro SEFAZ NFe: {e}")
                import traceback
                logger.error(traceback.format_exc())
                break

        # Persiste ultNSU final
        if self.db and self.empresa and ultNSU != "000000000000000":
            self.empresa.nfe_ultnsu = ultNSU
            self.db.commit()

        logger.info(f"SEFAZ retornou {len(resultados)} NFe (ultNSU={ultNSU})")
        return resultados

    def _salvar_local(self, chave, numero, dh_emi, valor, emitente_nome, emitente_cnpj,
                      destinatario_nome, destinatario_cnpj, nsu, schema, xml):
        if not self.db or not chave:
            return
        try:
            existente = self.db.query(NFeDistribuida).filter(NFeDistribuida.chave_acesso == chave).first()
            if existente:
                if xml and not existente.xml:
                    existente.xml = xml
                    existente.schema_nfe = schema
                    self.db.commit()
                return
            reg = NFeDistribuida(
                chave_acesso=chave,
                numero=numero,
                dh_emi=dh_emi,
                valor=valor,
                emitente_nome=emitente_nome,
                emitente_cnpj=emitente_cnpj,
                destinatario_nome=destinatario_nome,
                destinatario_cnpj=destinatario_cnpj,
                nsu=nsu,
                schema_nfe=schema,
                xml=xml,
            )
            self.db.add(reg)
            self.db.commit()
        except Exception as e:
            logger.warning(f"Erro ao salvar NFe local: {e}")
            self.db.rollback()

    def _extract_nfe_info(self, xml: str) -> tuple:
        chave = None
        numero = None
        dh_emi = None
        valor = None
        emitente_nome = None
        emitente_cnpj = None
        destinatario_nome = None
        destinatario_cnpj = None

        # Chave de acesso do atributo Id na NFe
        m = re.search(r'<[^:>]*:?NFe[^>]*Id="NFe(\d+)"', xml)
        if m: chave = m.group(1)

        m = re.search(r'<[^:>]*:?nNF[^>]*>(\d+)</', xml)
        if m: numero = m.group(1)
        m = re.search(r'<[^:>]*:?dhEmi[^>]*>([^<]+)</', xml)
        if m: dh_emi = m.group(1)
        m = re.search(r'<[^:>]*:?vNF[^>]*>([\d.]+)</', xml)
        if m: valor = float(m.group(1))

        # Emitente
        emit = re.search(r'<[^:>]*:?emit>(.*?)</[^:>]*:?emit>', xml, re.DOTALL)
        if emit:
            bloco = emit.group(1)
            m1 = re.search(r'<[^:>]*:?xNome[^>]*>(.*?)</', bloco)
            if m1: emitente_nome = m1.group(1)
            m2 = re.search(r'<[^:>]*:?CNPJ[^>]*>(\d+)</', bloco)
            if m2: emitente_cnpj = m2.group(1)

        # Destinatário
        dest = re.search(r'<[^:>]*:?dest>(.*?)</[^:>]*:?dest>', xml, re.DOTALL)
        if dest:
            bloco = dest.group(1)
            m1 = re.search(r'<[^:>]*:?xNome[^>]*>(.*?)</', bloco)
            if m1: destinatario_nome = m1.group(1)
            m2 = re.search(r'<[^:>]*:?CNPJ[^>]*>(\d+)</', bloco)
            if m2: destinatario_cnpj = m2.group(1)
            if not destinatario_cnpj:
                m2 = re.search(r'<[^:>]*:?CPF[^>]*>(\d+)</', bloco)
                if m2: destinatario_cnpj = m2.group(1)

        return chave, numero, dh_emi, valor, emitente_nome, emitente_cnpj, destinatario_nome, destinatario_cnpj
