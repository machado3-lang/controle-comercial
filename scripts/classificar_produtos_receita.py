"""Classifica retroativamente o cadastro de produtos/servicos.

Cada produto/servico recebe a conta de receita a que pertence:

  - `tipo == 'servico'`            -> 3.2 Prestacao de Servicos
  - `tipo` produto/kit, nome com
    indicio de servico              -> 3.2 Prestacao de Servicos
  - demais                         -> 3.1 Venda de Produtos

Efeito: as 160 contas a receber ja existentes passam a resolver pela origem
em vez de cair no fallback, e o DRE separa receita de produto de receita de
servico.

Idempotente: so escreve em produtos com `conta_receita_id` vazio.
Nao apaga nada; `--forcar` reescreve todas as classificacoes.
"""
import argparse
import logging
import os
import sys
import unicodedata

# Permite rodar o script de qualquer diretorio.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from models import PlanoDeContas, Produto

logger = logging.getLogger(__name__)

# Termos que indicam receita de servico em um item marcado como produto.
INDICIOS_SERVICO = (
    "servico", "servicos", "manutencao", "manutenção", "instalacao", "instalação",
    "reparo", "conserto", "assistencia técnica", "assistência técnica",
    "hora tecnica", "hora técnica", "treinamento", "capacitacao", "capacitação",
    "visita técnica", "visita tecnica", "orcamento", "orçamento", "laudo",
    "certificado", "homologacao", "homologação", "configuracao", "configuração",
    "suporte técnico", "suporte tecnico", "locacao", "locação", "mensalidade",
)


def _sem_acento(texto):
    return (unicodedata.normalize("NFKD", texto or "")
            .encode("ASCII", "ignore").decode().lower())


def _parece_servico(produto):
    nome = _sem_acento(produto.nome)
    return any(termo in nome for termo in INDICIOS_SERVICO)


def _contas_receita(db):
    """Mapeia '3.1' / '3.2' (ou equivalentes) para ids de contas de receita."""
    receitas = db.query(PlanoDeContas).filter(
        PlanoDeContas.tipo == "receita", PlanoDeContas.ativo == True
    ).order_by(PlanoDeContas.codigo).all()

    def por_codigo(codigo):
        for c in receitas:
            if c.codigo == codigo:
                return c
        return None

    produtos = por_codigo("3.1")
    servicos = por_codigo("3.2")

    # Tolerancia quando o plano do usuario usa outra numeracao: pega a primeira
    # conta folha de receita de cada ramo.
    if produtos is None:
        folhas = [c for c in receitas if c.nivel and c.nivel >= 2]
        produtos = folhas[0] if folhas else None
    if servicos is None and produtos is not None:
        folhas = [c for c in receitas if c.nivel and c.nivel >= 2 and c.id != produtos.id]
        servicos = folhas[0] if folhas else produtos

    return produtos, servicos


def main(forcar=False):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = SessionLocal()

    conta_produtos, conta_servicos = _contas_receita(db)
    if conta_produtos is None:
        logger.error("Nenhuma conta de receita ativa encontrada em plano_contas. "
                     "Cadastre o plano em /plano-contas antes de rodar.")
        db.close()
        return 1

    logger.info("Contas: produtos=%s (%s) | servicos=%s (%s)",
                conta_produtos.codigo, conta_produtos.nome,
                conta_servicos.codigo, conta_servicos.nome)

    query = db.query(Produto)
    if not forcar:
        query = query.filter(Produto.conta_receita_id.is_(None))

    por_tipo = {"servico": 0, "produto": 0, "sem_conta": 0}
    for produto in query.all():
        if produto.tipo == "servico":
            conta = conta_servicos
            por_tipo["servico"] += 1
        elif _parece_servico(produto):
            conta = conta_servicos
            por_tipo["produto"] += 1
        else:
            conta = conta_produtos
            por_tipo["produto"] += 1

        if conta is None:
            por_tipo["sem_conta"] += 1
            continue
        produto.conta_receita_id = conta.id

    db.commit()

    restantes = db.query(Produto).filter(Produto.conta_receita_id.is_(None)).count()
    logger.info("Classificados: %d servico, %d produto | sem conta=%d | restantes sem conta: %d",
                por_tipo["servico"], por_tipo["produto"], por_tipo["sem_conta"], restantes)
    db.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--forcar", action="store_true",
                   help="reescreve a conta de todos os produtos")
    raise SystemExit(main(p.parse_args().forcar))