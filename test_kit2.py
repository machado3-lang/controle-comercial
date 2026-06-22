from fastapi.testclient import TestClient
from main import app
import os

# Usar banco em memória
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

client = TestClient(app)

# Primeiro precisa popular alguns dados
# Vamos testar com dados existentes