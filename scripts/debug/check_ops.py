import os
from requests import Session
from requests.auth import HTTPBasicAuth

s = Session()
s.auth = HTTPBasicAuth(
    os.environ.get("BETHA_USUARIO", "50087320134"),
    os.environ.get("BETHA_SENHA", "Multi123com")
)

# ConsultarStatusDps com formato correto
soap = '''<soapenv:Envelope xmlns="http://www.betha.com.br/e-nota-dps" xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
   <soapenv:Header/>
   <soapenv:Body>
      <ConsultarStatusDpsEnvio>
         <tpAmb>1</tpAmb>
         <codigoIbge>5003702</codigoIbge>
         <cpfCnpjPrestador>13133714000110</cpfCnpjPrestador>
         <protocolo>842422864650725</protocolo>
         <tipoIntegracao>EMISSAO</tipoIntegracao>
      </ConsultarStatusDpsEnvio>
   </soapenv:Body>
</soapenv:Envelope>'''

r = s.post('https://nota-eletronica.betha.cloud/dps/ws', data=soap.encode(), headers={'Content-Type': 'text/xml; charset=utf-8'}, timeout=30)
print(f'Status: {r.status_code}')
print(f'Response:\n{r.text[:2000]}')