from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
import datetime, os
from asn1crypto import pkcs12 as apkcs12, keys, cms, core
from asn1crypto.core import OrderedDict
from services.pkcs12_pbe import encrypt_pbe
from services.cert_store import load_pfx_robust, _normalize_pfx

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u'teste')])
cert = (x509.CertificateBuilder().subject_name(subj).issuer_name(subj)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1)).not_valid_after(datetime.datetime(2030, 1, 1))
        .sign(key, hashes.SHA256()))
key_der = key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
cert_der = cert.public_bytes(Encoding.DER)
OID = '1.2.840.113549.1.12.1.3'
salt = os.urandom(8); iter_n = 2048; pw = 'senhaForte123'
enc_key = encrypt_pbe(key_der, OID, salt, iter_n, pw)
epki = keys.EncryptedPrivateKeyInfo({
    'encryption_algorithm': {'algorithm': OID, 'parameters': {'salt': salt, 'iterations': iter_n}},
    'encrypted_data': enc_key})
keybag = apkcs12.SafeBag({'bag_id': 'pkcs8_shrouded_key_bag', 'bag_value': epki})
certbag = apkcs12.SafeBag({'bag_id': 'cert_bag', 'bag_value': {'cert_id': '1.2.840.113549.1.12.10.1.1', 'cert_value': cert_der}})
safe = apkcs12.SafeContents([keybag, certbag])
enc_safe = encrypt_pbe(safe.dump(), OID, salt, iter_n, pw)
ed_ci = apkcs12.ContentInfo({
    'content_type': 'encrypted_data',
    'content': cms.EncryptedData({
        'version': 0,
        'encrypted_content_info': {'content_type': 'data', 'content_encryption_algorithm': {'algorithm': OID, 'parameters': {'salt': salt, 'iterations': iter_n}}, 'encrypted_content': enc_safe},
    })})
pfx = apkcs12.Pfx({
    'version': 3,
    'auth_safe': apkcs12.ContentInfo({
        'content_type': 'data',
        'content': apkcs12.AuthenticatedSafe([ed_ci]).dump()})})
pfx_bytes = pfx.dump()

pk, c, cas = load_pfx_robust(pfx_bytes, pw)
print('load_pfx_robust OK:', c.subject.rfc4514_string())
mod = _normalize_pfx(pfx_bytes, pw)
pk2, c2, cas2 = load_pfx_robust(mod, pw)
print('normalize+reload OK:', c2.subject.rfc4514_string())
print('TUDO OK')
