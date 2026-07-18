import logging
from datetime import datetime
from sqlalchemy.orm import Session
from models import AuditLog

logger = logging.getLogger(__name__)


def registrar_auditoria(
    db: Session,
    user_id: int | None,
    acao: str,
    entidade: str | None = None,
    entidade_id: int | None = None,
    detalhes: str | None = None,
    ip: str | None = None,
) -> AuditLog:
    log = AuditLog(
        user_id=user_id,
        acao=acao,
        entidade=entidade,
        entidade_id=entidade_id,
        detalhes=detalhes,
        ip=ip,
        created_at=datetime.now(),
    )
    db.add(log)
    db.commit()
    logger.info(
        f"Audit: user={user_id} acao={acao} entidade={entidade}/{entidade_id}"
    )
    return log
