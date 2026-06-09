import hashlib
import secrets

senha = 'admin123'
hash_armazenado = 'e99a18c428cb38d5f26b06547c0295c2c0b8d5f26b06547c0295c2c0b8d5f26b0'

# legacy format
if ':' not in hash_armazenado:
    result = hash_armazenado == hashlib.sha256(senha.encode()).hexdigest()
    print(f'Legacy check: {result}')
    print(f'Expected: {hashlib.sha256(senha.encode()).hexdigest()}')
    print(f'Stored: {hash_armazenado}')