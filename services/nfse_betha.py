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
from typing import Optional
from lxml import etree
from cryptography.hazmat.primitives.serialization import pkcs12
from signxml import XMLSigner, methods
from requests import Session
from requests.auth import HTTPBasicAuth
import warnings
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
warnings.filterwarnings('ignore')

class NFSeBethaError(Exception):
    pass

BETHA_NFSE_URL = os.getenv('BETHA_NFSE_DPS_URL', 'https://nota-eletronica.betha.cloud/dps/ws')

class BethaNfseService:
    def __init__(self):
        self.usuario = os.getenv('BETHA_USUARIO')
        self.senha = os.getenv('BETHA_SENHA')
        self.cert_path = os.getenv('CERT_PATH', './certs/certificado.pfx')
        self.cert_password = os.getenv('CERT_PASSWORD')
        if not self.usuario or not self.senha:
            raise NFSeBethaError("Credenciais Betha não configuradas no .env")

    def _get_pem_combined(self) -> str:
        from cryptography.hazmat.primitives import serialization
        with open(self.cert_path, 'rb') as f:
            pfx_data = f.read()
        private_key, cert, _ = pkcs12.load_key_and_certificates(
            pfx_data, 
            password=self.cert_password.encode() if self.cert_password else None
        )
        import tempfile
        combined = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ) + cert.public_bytes(serialization.Encoding.PEM)
        tmp_path = os.path.join(tempfile.gettempdir(), 'cert_combined.pem')
        with open(tmp_path, 'wb') as f:
            f.write(combined)
        return tmp_path

    def _get_session(self) -> Session:
        session = Session()
        if os.path.exists(self.cert_path):
            pem_path = self._get_pem_combined()
            session.cert = pem_path
        session.auth = HTTPBasicAuth(self.usuario, self.senha)
        return session

    def gerar_id_dps(self, cmun: str, cnpj: str, serie: str, ndps: str) -> str:
        """Gera ID DPS no formato: DPS + cMun(7) + série(1) + CNPJ(14) + 0000 + série(1) + nDPS(15) = 45 chars"""
        p1 = f'{serie}{cnpj}'
        p2 = f'0000{serie}{ndps}'
        return f'DPS{cmun}{p1}{p2}'

    def enviar_dps(self, dps_xml: str, tpAmb: int = 1) -> dict:
        try:
            logger.info(f"Enviando DPS para Betha (tpAmb={tpAmb})...")
            session = self._get_session()
            session.verify = False
            
            soap_xml = f'''<soapenv:Envelope xmlns="http://www.betha.com.br/e-nota-dps" xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
   <soapenv:Header/>
   <soapenv:Body>
      <RecepcionarDpsEnvio>
         {dps_xml}
      </RecepcionarDpsEnvio>
   </soapenv:Body>
</soapenv:Envelope>'''
            
            response = session.post(
                BETHA_NFSE_URL,
                data=soap_xml.encode('utf-8'),
                headers={'Content-Type': 'text/xml; charset=utf-8'},
                timeout=60
            )
            response.raise_for_status()
            root = etree.fromstring(response.content)
            ns = '{http://www.betha.com.br/e-nota-dps}'
            protocolo_el = root.find(f'.//{ns}protocolo')
            if protocolo_el is not None:
                logger.info(f"Protocolo recebido: {protocolo_el.text}")
                return {'protocolo': protocolo_el.text, 'status': 'sucesso'}
            lista_msg = root.find(f'.//{ns}listaMensagens')
            if lista_msg is not None:
                mensagens = root.findall(f'.//{ns}mensagem')
                erros = []
                for m in mensagens:
                    cod = m.find(f'{ns}codigo')
                    msg = m.find(f'{ns}mensagem')
                    erros.append({'codigo': cod.text if cod is not None else 'N/A', 'mensagem': msg.text if msg is not None else 'N/A'})
                    logger.error(f"Erro DPS {cod.text}: {msg.text}")
                return {'protocolo': None, 'erros': erros}
            raise NFSeBethaError("Protocolo não retornado")
        except Exception as e:
            logger.error(f"Erro SOAP: {e}")
            raise NFSeBethaError(f"Erro SOAP: {e}")

    def consultar_status(self, protocolo: str, tpAmb: int = 1) -> dict:
        """Consulta status da DPS enviada"""
        try:
            logger.info(f"Consultando status da DPS {protocolo}...")
            session = self._get_session()
            session.verify = False
            
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
            response.raise_for_status()
            root = etree.fromstring(response.content)
            ns = '{http://www.betha.com.br/e-nota-dps}'
            status_el = root.find(f'.//{ns}statusProcessamento')
            if status_el is not None:
                data_hora = root.find(f'.//{ns}dataHoraRecebimento')
                return {'status': status_el.text, 'data_hora': data_hora.text if data_hora is not None else None}
            lista_msg = root.find(f'.//{ns}listaMensagens')
            if lista_msg is not None:
                mensagens = root.findall(f'.//{ns}mensagem')
                for m in mensagens:
                    msg_el = m.find(f'{ns}mensagem')
                    if msg_el is not None:
                        return {'status': 'ERRO', 'mensagem': msg_el.text}
            return {'status': 'DESCONHECIDO'}
        except Exception as e:
            logger.error(f"Erro consulta: {e}")
            raise NFSeBethaError(f"Erro consulta: {e}")

def gerar_dps_xml(pedido, db, tpAmb: int = 1) -> str:
    """Gera XML DPS Nacional - formato ID 45 chars - filtra apenas serviços"""
    from models import Empresa
    empresa = db.query(Empresa).first()
    
    itens_servico = [i for i in pedido.itens if i.produto and i.produto.tipo == 'servico']
    vlr = sum(float(i.total or 0) for i in itens_servico)
    if vlr == 0:
        vlr = float(pedido.total or 0)
    
    cod_serv = '010101'
    desc_serv = 'Servicos de internet'
    for item in itens_servico:
        if item.produto and item.produto.codigo_lc116:
            cod_serv = ''.join(filter(str.isdigit, item.produto.codigo_lc116)).zfill(6)
            desc_serv = item.produto.nome or item.descricao or desc_serv
            break
    
    cnpj_prest = ''.join(filter(str.isdigit, empresa.cnpj or ''))
    cpf_cnpj_toma = ''.join(filter(str.isdigit, pedido.cliente.cpf_cnpj or ''))
    
    if len(cpf_cnpj_toma) == 11:
        toma_doc = f'<CPF>{cpf_cnpj_toma}</CPF>'
    else:
        toma_doc = f'<CNPJ>{cpf_cnpj_toma}</CNPJ>'
    
    cmun = os.getenv('MUNICIPIO_CODIGO', '5003702')
    serie = '1'
    ndps = f"{pedido.id:015d}"
    
    service = BethaNfseService()
    id_dps = service.gerar_id_dps(cmun, cnpj_prest, serie, ndps)
    
    return f'''<DPS xmlns="http://www.betha.com.br/e-nota-dps" versao="1.01">
   <infDPS id="{id_dps}">
      <tpAmb>{tpAmb}</tpAmb>
      <dhEmi>{pedido.data.strftime('%Y-%m-%dT%H:%M:%S')}</dhEmi>
      <verAplic>fly_WS_1.1.0</verAplic>
      <serie>{serie}</serie>
      <nDPS>{ndps}</nDPS>
      <dCompet>{pedido.data.strftime('%Y-%m-%d')}</dCompet>
      <tpEmit>1</tpEmit>
      <cLocEmi>{cmun}</cLocEmi>
      <prest>
         <CNPJ>{cnpj_prest}</CNPJ>
         <regTrib>
            <opSimpNac>1</opSimpNac>
            <regEspTrib>0</regEspTrib>
         </regTrib>
      </prest>
      <toma>
         {toma_doc}
         <xNome>{pedido.cliente.nome or ''}</xNome>
      </toma>
      <serv>
         <locPrest>
            <cLocPrestacao>{cmun}</cLocPrestacao>
         </locPrest>
         <cServ>
            <cTribNac>{cod_serv}</cTribNac>
            <xDescServ>{desc_serv}</xDescServ>
            <cNBS>101011200</cNBS>
         </cServ>
      </serv>
      <valores>
         <vServPrest>
            <vServ>{vlr:.2f}</vServ>
         </vServPrest>
         <trib>
            <tribMun>
               <tribISSQN>1</tribISSQN>
               <pAliq>2.00</pAliq>
               <tpRetISSQN>1</tpRetISSQN>
            </tribMun>
            <totTrib>
               <indTotTrib>0</indTotTrib>
            </totTrib>
         </trib>
      </valores>
      <IBSCBS>
         <finNFSe>0</finNFSe>
         <cIndOp>050102</cIndOp>
         <indDest>0</indDest>
         <valores>
            <trib>
               <gIBSCBS>
                  <CST>000</CST>
                  <cClassTrib>000001</cClassTrib>
               </gIBSCBS>
            </trib>
         </valores>
      </IBSCBS>
   </infDPS>
</DPS>'''

def emitir_completa(pedido, db, tpAmb: int = 1) -> dict:
    try:
        service = BethaNfseService()
        dps_xml = gerar_dps_xml(pedido, db, tpAmb)
        resultado = service.enviar_dps(dps_xml, tpAmb)
        return {
            'protocolo': resultado.get('protocolo'),
            'numero': None,
            'codigo_verificacao': None,
            'xml': dps_xml,
            'data_emissao': datetime.now(),
            'erros': resultado.get('erros', [])
        }
    except NFSeBethaError:
        raise
    except Exception as e:
        raise NFSeBethaError(f"Erro inesperado: {e}")