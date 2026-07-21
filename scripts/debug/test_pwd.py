from app.core.security import hash_senha, verifica_senha
print('Testing admin123:')
hash_val = hash_senha('admin123')
print(f'Hash: {hash_val[:50]}')
print(f'Verify: {verifica_senha("admin123", "2:600000:40edf6ae327d9579c994f297dca322e8:04fccf82")}')