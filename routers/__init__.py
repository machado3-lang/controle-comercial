from fastapi import APIRouter

from . import auth, clientes, fornecedores, produtos, ordens_servico, pedidos, assinaturas
from . import contas, configuracoes, bling, sicoob, nfe, nfse, planocontas, tipos_documento
from . import consolidacoes

router = APIRouter()

router.include_router(auth.router)
router.include_router(clientes.router)
router.include_router(fornecedores.router)
router.include_router(produtos.router)
router.include_router(ordens_servico.router)
router.include_router(pedidos.router)
router.include_router(assinaturas.router)
router.include_router(contas.router)
router.include_router(configuracoes.router)
router.include_router(bling.router)
router.include_router(sicoob.router)
router.include_router(nfe.router)
router.include_router(nfse.router)
router.include_router(planocontas.router)
router.include_router(tipos_documento.router)
router.include_router(consolidacoes.router)