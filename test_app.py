from main import app
print('App carregado com sucesso!')
print('Rotas:')
for route in app.routes:
    if hasattr(route, 'path') and 'consolid' in route.path:
        print(f'  {route.methods} {route.path}')