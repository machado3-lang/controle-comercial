import os
os.environ['SECRET_KEY'] = 'test'

from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    try:
        conn.execute(text('ALTER TABLE produtos ADD COLUMN tipo VARCHAR(20) DEFAULT "produto"'))
        conn.commit()
        print('Coluna tipo adicionada')
    except Exception as e:
        print(f'Erro: {e}')