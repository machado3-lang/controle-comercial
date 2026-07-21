"""
Corrige duplicatas NFSe causadas por chave_acesso nao salva na sincronizacao.

O que faz:
1. Para cada par (avulsa + adn duplicata): copia xml_text e chave_acesso
   da ADN para a Avulsa (se a Avulsa nao tiver)
2. Remove os registros ADN duplicados
3. Tudo numa unica transacao com rollback em caso de erro

Uso:
  python scripts/fix_nfse_duplicatas.py          # executa
  python scripts/fix_nfse_duplicatas.py --dry-run # mostra o que faria
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models
from database import get_db
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DRY_RUN = '--dry-run' in sys.argv

def fix():
    db = next(get_db())
    try:
        dups = db.execute(text("""
            SELECT n1.id AS avulsa_id, n2.id AS adn_id,
                   n1.numero,
                   n1.xml_text IS NOT NULL AS avulsa_tem_xml,
                   n2.xml_text IS NOT NULL AS adn_tem_xml,
                   n1.chave_acesso IS NOT NULL AS avulsa_tem_chave,
                   n2.chave_acesso IS NOT NULL AS adn_tem_chave,
                   n1.pdf_path, n2.pdf_path
            FROM nfse n1
            JOIN nfse n2 ON n1.codigo_verificacao = n2.codigo_verificacao
                       AND n2.origem = 'adn'
            WHERE n1.origem = 'avulsa'
              AND n1.codigo_verificacao IS NOT NULL
              AND n2.codigo_verificacao IS NOT NULL
            ORDER BY n1.numero
        """)).all()

        if not dups:
            logger.info("Nenhuma duplicata encontrada.")
            return

        logger.info(f"Encontradas {len(dups)} duplicatas.")

        updates_xml = 0
        updates_chave = 0
        to_delete = []
        actions = []

        for d in dups:
            avulsa_id, adn_id = d[0], d[1]
            actions.append(f"  NFSe #{d[2]}: avulsa ID={avulsa_id} <- ADN ID={adn_id}")

            if not d[4]:  # adn_tem_xml
                logger.warning(f"  ADN ID={adn_id} nao tem xml_text, pulando")
                continue

            if not d[3]:  # avulsa_tem_xml
                if not DRY_RUN:
                    db.execute(
                        text("UPDATE nfse SET xml_text = (SELECT xml_text FROM nfse WHERE id = :adn_id) WHERE id = :avulsa_id"),
                        {"adn_id": adn_id, "avulsa_id": avulsa_id}
                    )
                updates_xml += 1
                actions[-1] += " [xml_text copiado]"

            if not d[5]:  # avulsa_tem_chave
                if not DRY_RUN:
                    db.execute(
                        text("UPDATE nfse SET chave_acesso = (SELECT chave_acesso FROM nfse WHERE id = :adn_id) WHERE id = :avulsa_id"),
                        {"adn_id": adn_id, "avulsa_id": avulsa_id}
                    )
                updates_chave += 1
                actions[-1] += " [chave_acesso copiado]"

            to_delete.append(adn_id)

        if DRY_RUN:
            logger.info("=== DRY RUN - nenhuma alteracao sera feita ===")
            for a in actions:
                print(a)
            logger.info(f"\nResumo dry-run: {updates_xml} xml_text a copiar, {updates_chave} chave_acesso a copiar, {len(to_delete)} registros a remover")
            return

        # Executa
        logger.info(f"Copiando xml_text: {updates_xml}")
        logger.info(f"Copiando chave_acesso: {updates_chave}")

        for adn_id in to_delete:
            db.execute(text("DELETE FROM nfse_itens WHERE nfse_id = :id"), {"id": adn_id})
            db.execute(text("DELETE FROM nfse WHERE id = :id"), {"id": adn_id})

        db.commit()
        logger.info(f"Removidos {len(to_delete)} registros ADN duplicados.")
        logger.info("Concluido com sucesso!")

    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Erro no banco, transacao revertida: {e}")
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro inesperado, transacao revertida: {e}")
        raise
    finally:
        db.close()

if __name__ == '__main__':
    fix()
