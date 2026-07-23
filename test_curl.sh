#!/bin/bash
cd "C:\Controle de Serviços"

# Login
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "email=admin@admin.com&senha=admin123" \
  -c cookies.txt -s -v 2>&1 | grep -E "HTTP|Set-Cookie|< title"

# Get NFe emit page
echo "=== NFe emit page ==="
curl -b cookies.txt http://localhost:8000/nfe/emitir/consolidacao/2 -s | head -50

echo ""
echo "=== NFSe emit page ==="
curl -b cookies.txt http://localhost:8000/nfse/emitir/consolidacao/2 -s | head -50