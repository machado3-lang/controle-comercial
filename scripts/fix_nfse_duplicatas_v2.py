"""
Corrige TODAS as duplicatas NFSe (por numero ou codigo_verificacao).

Uso:
  python scripts/fix_nfse_duplicatas_v2.py          # executa
  python scripts/fix_nfse_duplicatas_v2.py --dry-run # mostra o que faria
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
        # Encontra duplicatas por numero
        dups = db.execute(text("""
            SELECT numero, COUNT(*) AS cnt
            FROM nfse 
            WHERE numero IS NOT NULL AND numero != ''
            GROUP BY numero 
            HAVING COUNT(*) > 1
            ORDER BY numero
        """)).all()

        if not dups:
            logger.info("Nenhuma duplicata por numero encontrada.")
            # Tenta por codigo_verificacao
            dups2 = db.execute(text("""
                SELECT codigo_verificacao, COUNT(*) AS cnt
                FROM nfse 
                WHERE codigo_verificacao IS NOT NULL
                GROUP BY codigo_verificacao 
                HAVING COUNT(*) > 1
                ORDER BY COUNT(*) DESC
            """)).all()
            if not dups2:
                logger.info("Nenhuma duplicata por codigo_verificacao. Banco limpo!")
                return
            logger.info(f"Encontradas {len(dups2)} duplicatas por codigo_verificacao.")
            grupos = dups2
            campo = 'codigo_verificacao'
        else:
            logger.info(f"Encontradas {len(dups)} duplicatas por numero.")
            grupos = dups
            campo = 'numero'

        total_removidas = 0
        total_backfill = 0

        for g in grupos:
            chave = g[0]
            records = db.execute(text(f"""
                SELECT id, origem, numero, 
                       xml_text IS NOT NULL AS tem_xml,
                       pdf_path IS NOT NULL AS tem_pdf,
                       chave_acesso IS NOT NULL AS tem_chave,
                       codigo_verificacao IS NOT NULL AS tem_cv
                FROM nfse 
                WHERE {campo} = :chave
                ORDER BY 
                    CASE origem 
                        WHEN 'avulsa' THEN 1
                        WHEN 'pedido' THEN 2
                        WHEN 'assinatura' THEN 3
                        WHEN 'os' THEN 4
                        WHEN 'consolidacao' THEN 5
                        WHEN 'adn' THEN 6
                        ELSE 7
                    END,
                    tem_xml DESC, tem_pdf DESC
            """), {"chave": chave}).all()

            if len(records) <= 1:
                continue

            # O primeiro registro e' o "bom" (maior prioridade de origem + dados)
            keeper = records[0]
            to_delete = records[1:]

            logger.info(f"  #{campo}={str(chave)[:25]}... keeper ID={keeper[0]} origem={keeper[1]} -> remover {len(to_delete)}")

            for rec in to_delete:
                # Backfill: copia dados que o keeper nao tem
                backfill_fields = []
                if rec[3] and not keeper[3]:  # tem_xml
                    if not DRY_RUN:
                        db.execute(text("UPDATE nfse SET xml_text = (SELECT xml_text FROM nfse WHERE id = :src_id) WHERE id = :dst_id"),
                                   {"src_id": rec[0], "dst_id": keeper[0]})
                    backfill_fields.append('xml_text')
                if rec[5] and not keeper[5]:  # tem_chave
                    if not DRY_RUN:
                        db.execute(text("UPDATE nfse SET chave_acesso = (SELECT chave_acesso FROM nfse WHERE id = :src_id) WHERE id = :dst_id"),
                                   {"src_id": rec[0], "dst_id": keeper[0]})
                    backfill_fields.append('chave_acesso')
                if rec[6] and not keeper[6]:  # tem_cv
                    if not DRY_RUN:
                        db.execute(text("UPDATE nfse SET codigo_verificacao = (SELECT codigo_verificacao FROM nfse WHERE id = :src_id) WHERE id = :dst_id"),
                                   {"src_id": rec[0], "dst_id": keeper[0]})
                    backfill_fields.append('codigo_verificacao')

                if backfill_fields:
                    logger.info(f"    -> backfill {backfill_fields} de ID={rec[0]} para ID={keeper[0]}")
                    total_backfill += 1

                # Remove itens e o registro duplicado
                if not DRY_RUN:
                    db.execute(text("DELETE FROM nfse_itens WHERE nfse_id = :id"), {"id": rec[0]})
                    db.execute(text("DELETE FROM nfse WHERE id = :id"), {"id": rec[0]})
                total_removidas += 1

        if DRY_RUN:
            logger.info(f"\n= DRY RUN - nenhuma alteracao =")
            logger.info(f"  Removeria: {total_removidas} registros")
            logger.info(f"  Backfill: {total_backfill} campos")
            return

        db.commit()
        logger.info(f"\n= CONCLUIDO =")
        logger.info(f"  Removidas: {total_removidas} duplicatas")
        logger.info(f"  Backfill: {total_backfill} registros")

        # Verificacao final
        restam = db.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT numero FROM nfse 
                WHERE numero IS NOT NULL AND numero != ''
                GROUP BY numero HAVING COUNT(*) > 1
            ) d
        """)).scalar()
        if restam:
            logger.warning(f"AVISO: Ainda restam {restam} duplicatas por numero!")
        else:
            logger.info("Banco limpo: 0 duplicatas restantes.")

    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Erro no banco, transacao revertida: {e}")
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro inesperado, transacao revertida: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        db.close()

if __name__ == '__main__':
    fix()
