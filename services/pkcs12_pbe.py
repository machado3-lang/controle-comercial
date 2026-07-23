"""
PKCS#12 PBE (password-based encryption) decoder, independente do OpenSSL.

Motivo: OpenSSL 3.0 (usado no Railway) rejeita o MAC de alguns PFX A1
(cifra antiga RC2/3DES + MAC novo) com "Mac verify error", mesmo com a
senha correta. Esta implementacao segue o RFC 7292 (Appendix B.2/B.3) e
DESCRIPTOGRAFA os *bags* SEM verificar o MAC, contornando o problema.

Suporta os algoritmos PKCS12 PBE baseados em SHA-1:
  1.2.840.113549.1.12.1.3 -> 3-key 3DES-CBC
  1.2.840.113549.1.12.1.4 -> 2-key 3DES-CBC
  1.2.840.113549.1.12.1.5 -> RC2-CBC 128-bit
  1.2.840.113549.1.12.1.6 -> RC2-CBC 40-bit
"""
import hashlib

# OID -> (cipher, key_len_bytes, iv_len_bytes, rc2_effective_bits)
_PBE_CIPHERS = {
    "1.2.840.113549.1.12.1.3": ("3des", 24, 8, 0),
    "1.2.840.113549.1.12.1.4": ("3des2", 16, 8, 0),
    "1.2.840.113549.1.12.1.5": ("rc2", 16, 8, 128),
    "1.2.840.113549.1.12.1.6": ("rc2", 5, 8, 40),
}


def _bmpstring(password: str) -> bytes:
    """RFC 7292 B.1: senha como BMPString (UTF-16BE) + terminador nulo 2 bytes."""
    return password.encode("utf-16-be") + b"\x00\x00"


def _pkcs12_kdf(password_bytes: bytes, salt: bytes, iterations: int,
                id_byte: int, n_bits: int) -> bytes:
    """RFC 7292 B.2 - deriva n_bits de material pseudoaleatorio (SHA-1)."""
    H = hashlib.sha1
    u = 20          # bytes (160 bits)
    v = 64          # bytes (512 bits)
    n_bytes = (n_bits + 7) // 8
    D = bytes([id_byte]) * v

    def _repeat(data: bytes, total: int) -> bytes:
        if len(data) == 0:
            return b""
        reps = (total + len(data) - 1) // len(data)
        return (data * reps)[:total]

    S = _repeat(salt, v * ((len(salt) + v - 1) // v)) if salt else b""
    P = _repeat(password_bytes, v * ((len(password_bytes) + v - 1) // v)) if password_bytes else b""
    I = bytearray(S + P)
    c = (n_bits + u * 8 - 1) // (u * 8)
    out = b""
    for _ in range(c):
        x = D + bytes(I)
        for _ in range(iterations):
            x = H(x).digest()
        Ai = x
        B = _repeat(Ai, v)
        Bint = int.from_bytes(B, "big")
        mod = 1 << (v * 8)
        for j in range(0, len(I), v):
            blk = I[j:j + v]
            val = (int.from_bytes(blk, "big") + Bint + 1) % mod
            I[j:j + v] = val.to_bytes(v, "big")
        out += Ai
    return out[:n_bytes]


def _derive_key_iv(password_bytes, salt, iterations, cipher, key_len, iv_len):
    """Tenta duas convencoes (OpenSSL: ID=1 p/ key, ID=2 p/ iv; e RFC combinado)
    e retorna (key, iv). Quem valida e' o padding apos decriptar."""
    strategies = []
    # Estrategia 1: separado (OpenSSL)
    k1 = _pkcs12_kdf(password_bytes, salt, iterations, 1, key_len * 8)
    iv1 = _pkcs12_kdf(password_bytes, salt, iterations, 2, iv_len * 8)
    strategies.append((k1, iv1))
    # Estrategia 2: combinado ID=1
    comb = _pkcs12_kdf(password_bytes, salt, iterations, 1, (key_len + iv_len) * 8)
    strategies.append((comb[:key_len], comb[key_len:]))
    return strategies


def _pkcs7_valid(padded: bytes) -> bool:
    if not padded or len(padded) % 8 != 0:
        return False
    pad = padded[-1]
    if pad < 1 or pad > 8:
        return False
    return padded[-pad:] == bytes([pad]) * pad


def _unpad(padded: bytes) -> bytes:
    pad = padded[-1]
    return padded[:-pad]


def _tripledes():
    """TripleDES pode estar em cryptography.hazmat.primitives.ciphers.algorithms
    ou (>=43) em cryptography.hazmat.decrepit.ciphers.algorithms."""
    try:
        from cryptography.hazmat.primitives.ciphers import algorithms
        return algorithms.TripleDES
    except Exception:
        from cryptography.hazmat.decrepit.ciphers import algorithms
        return algorithms.TripleDES
    PITABLE = [
        0xd9,0x78,0xf9,0xc4,0x19,0xdd,0xb5,0xed,0x28,0xe9,0xfd,0x79,0x4a,0xa0,0xd8,0x9d,
        0xc6,0x7e,0x37,0x83,0x2b,0x76,0x53,0x8e,0x62,0x4c,0x64,0x88,0x44,0x8b,0xfb,0xa2,
        0x17,0x9a,0x59,0xf5,0x87,0xb3,0x4f,0x13,0x61,0x45,0x6d,0x8d,0x09,0x81,0x7d,0x32,
        0xbd,0x8f,0x40,0xeb,0x86,0xb7,0x7b,0x0b,0xf0,0x95,0x21,0x22,0x5c,0x6b,0x4e,0x82,
        0x54,0xd6,0x65,0x93,0xce,0x60,0xb2,0x1c,0x73,0x56,0xc0,0x14,0xa7,0x8c,0xf1,0xdc,
        0x12,0x75,0xca,0x1f,0x3b,0xbe,0xe4,0xd1,0x42,0x3d,0xd4,0x30,0xa3,0x3c,0xb6,0x26,
        0x6f,0xbf,0x0e,0xda,0x46,0x69,0x07,0x57,0x27,0xf2,0x1d,0x9b,0xbc,0x94,0x43,0x03,
        0xf8,0x11,0xc7,0xf6,0x90,0xef,0x3e,0xe7,0x06,0xc3,0xd5,0x2f,0xc8,0x66,0x1e,0xd7,
        0x08,0xe8,0xea,0xde,0x80,0x52,0xee,0xf7,0x84,0xaa,0x72,0xac,0x35,0x4d,0x6a,0x2a,
        0x96,0x1a,0xd2,0x71,0x5a,0x15,0x49,0x74,0x4b,0x9f,0xd0,0x5e,0x04,0x18,0xa4,0xec,
        0xc2,0xe0,0x41,0x6e,0x0f,0x51,0xcb,0xcc,0x24,0x91,0xaf,0x50,0xa1,0xf4,0x70,0x39,
        0x99,0x7c,0x3a,0x85,0x23,0xb8,0xb4,0x7a,0xfc,0x02,0x36,0x5b,0x25,0x55,0x97,0x31,
        0x2d,0x5d,0xfa,0x98,0xe3,0x8a,0x92,0xae,0x05,0xdf,0x29,0x10,0x67,0x6c,0xba,0xc9,
        0xd3,0x00,0xe6,0xcf,0xe1,0x9e,0xa8,0x2c,0x63,0x16,0x01,0x3f,0x58,0xe2,0x89,0xa9,
        0x0d,0x38,0x34,0x1b,0xab,0x33,0xff,0xb0,0xbb,0x48,0x0c,0x5f,0xb9,0xb1,0xcd,0x2e,
        0xc5,0xf3,0xdb,0x47,0xe5,0xa5,0x9c,0x77,0x0a,0xa6,0x20,0x68,0xfe,0x7f,0xc1,0xad,
    ]
    T = len(key)
    T1 = effective_bits
    T8 = (T1 + 7) // 8
    TM = 255 % (1 << (8 + T1 - 8 * T8))
    L = bytearray(key)
    while len(L) < 128:
        i = len(L)
        L.append(PITABLE[(L[i - 1] + L[i - T]) & 0xFF])
    L[128 - T8] = PITABLE[L[128 - T8] & TM]
    for i in range(127 - T8, -1, -1):
        L[i] = PITABLE[L[i + 1] ^ L[i + T8]]
    # K[i] = L[2i] + 256*L[2i+1]
    K = [L[2 * i] + 256 * L[2 * i + 1] for i in range(64)]
    return K


def _rc2_decrypt_block(block: bytes, K: list) -> bytes:
    s = [1, 2, 3, 5]
    R = [block[0] + 256 * block[1], block[2] + 256 * block[3],
         block[4] + 256 * block[5], block[6] + 256 * block[7]]
    j = 63

    def ror(x, k):
        return ((x >> k) | ((x << (16 - k)) & 0xFFFF)) & 0xFFFF

    def r_mix(i):
        nonlocal j
        R[i] = ror(R[i], s[i])
        R[i] = (R[i] - K[j] - (R[i - 1] & R[i - 2]) - ((~R[i - 1]) & R[i - 3])) & 0xFFFF
        j -= 1

    def r_mash(i):
        R[i] = (R[i] - K[R[i - 1] & 63]) & 0xFFFF

    def r_round():
        r_mix(3); r_mix(2); r_mix(1); r_mix(0)
    def r_mround():
        r_mash(3); r_mash(2); r_mash(1); r_mash(0)

    for _ in range(5):
        r_round()
    r_mround()
    for _ in range(6):
        r_round()
    r_mround()
    for _ in range(5):
        r_round()
    return bytes([R[0] & 0xFF, (R[0] >> 8) & 0xFF, R[1] & 0xFF, (R[1] >> 8) & 0xFF,
                  R[2] & 0xFF, (R[2] >> 8) & 0xFF, R[3] & 0xFF, (R[3] >> 8) & 0xFF])


def encrypt_pbe(plaintext: bytes, oid: str, salt: bytes, iterations: int,
                 password: str) -> bytes:
    """Inverso de decrypt_pbe: cifra com PKCS7 padding usando o KDF RFC 7292."""
    spec = _PBE_CIPHERS.get(oid)
    if spec is None:
        raise ValueError(f"Algoritmo PBE nao suportado: {oid}")
    cipher, key_len, iv_len, rc2_bits = spec
    from cryptography.hazmat.primitives.padding import PKCS7
    padder = PKCS7(64).padder()
    padded = padder.update(plaintext) + padder.finalize()
    pw = _bmpstring(password)
    # Convencao OpenSSL/arquivos reais: ID=1 para a chave, ID=2 para o IV (separados).
    key, iv = _derive_key_iv(pw, salt, iterations, cipher, key_len, iv_len)[0]
    if cipher in ("3des", "3des2"):
        if cipher == "3des2":
            key = key + key[:8]
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
        c = Cipher(_tripledes()(key), modes.CBC(iv))
        e = c.encryptor()
        return e.update(padded) + e.finalize()
    elif cipher == "rc2":
        return _rc2_cbc_encrypt(padded, key, iv, rc2_bits)
    raise ValueError("cipher nao suportado")


def _rc2_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes, effective_bits: int) -> bytes:
    K = _rc2_keyexp(key, effective_bits)
    out = bytearray()
    prev = iv
    from cryptography.hazmat.primitives.padding import PKCS7
    padder = PKCS7(64).padder()
    data = padder.update(plaintext) + padder.finalize()
    for off in range(0, len(data), 8):
        block = data[off:off + 8]
        x = bytes(a ^ b for a, b in zip(block, prev))
        enc = _rc2_encrypt_block(x, K)
        out += enc
        prev = enc
    return bytes(out)


def _rc2_encrypt_block(block: bytes, K: list) -> bytes:
    s = [1, 2, 3, 5]
    R = [block[0] + 256 * block[1], block[2] + 256 * block[3],
         block[4] + 256 * block[5], block[6] + 256 * block[7]]
    j = 0

    def rol(x, k):
        return ((x << k) | (x >> (16 - k))) & 0xFFFF

    def mix(i):
        nonlocal j
        R[i] = (R[i] + K[j] + (R[i - 1] & R[i - 2]) + ((~R[i - 1]) & R[i - 3])) & 0xFFFF
        j += 1
        R[i] = rol(R[i], s[i])

    def mash(i):
        R[i] = (R[i] + K[R[i - 1] & 63]) & 0xFFFF

    def round_():
        mix(0); mix(1); mix(2); mix(3)
    def mround():
        mash(0); mash(1); mash(2); mash(3)

    for _ in range(5):
        round_()
    mround()
    for _ in range(6):
        round_()
    mround()
    for _ in range(5):
        round_()
    return bytes([R[0] & 0xFF, (R[0] >> 8) & 0xFF, R[1] & 0xFF, (R[1] >> 8) & 0xFF,
                  R[2] & 0xFF, (R[2] >> 8) & 0xFF, R[3] & 0xFF, (R[3] >> 8) & 0xFF])



def decrypt_pbe(encrypted: bytes, oid: str, salt: bytes, iterations: int,
                password: str, validate=None):
    """Descriptografa um blob PKCS12-PBE.
    Retorna os bytes decifrados (sem padding). `validate` (opcional) e um
    callable que recebe os bytes decifrados e retorna True se forem validos
    (ex.: parser como SafeContents/PrivateKeyInfo). Se None, valida só o padding."""
    spec = _PBE_CIPHERS.get(oid)
    if spec is None:
        raise ValueError(f"Algoritmo PBE nao suportado: {oid}")
    cipher, key_len, iv_len, rc2_bits = spec
    pw = password if isinstance(password, (bytes, bytearray)) else _bmpstring(password)
    candidates = _derive_key_iv(pw, salt, iterations, cipher, key_len, iv_len)
    for key, iv in candidates:
        try:
            if cipher == "3des":
                from cryptography.hazmat.primitives.ciphers import Cipher, modes
                c = Cipher(_tripledes()(key), modes.CBC(iv))
                d = c.decryptor()
                plain = d.update(encrypted) + d.finalize()
            elif cipher == "3des2":
                key24 = key + key[:8]  # 2-key 3DES -> K1,K2,K1
                from cryptography.hazmat.primitives.ciphers import Cipher, modes
                c = Cipher(_tripledes()(key), modes.CBC(iv))
                d = c.decryptor()
                plain = d.update(encrypted) + d.finalize()
            elif cipher == "rc2":
                plain = _rc2_cbc_decrypt(encrypted, key, iv, rc2_bits)
            else:
                continue
            if not _pkcs7_valid(plain):
                continue
            plain = _unpad(plain)
            if validate is not None:
                if not validate(plain):
                    continue
            return plain
        except Exception:
            continue
    raise ValueError("Falha ao descriptografar bag PBE (senha ou algoritmo)")
