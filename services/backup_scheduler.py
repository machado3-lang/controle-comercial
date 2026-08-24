"""
Backup agendado e gestão de arquivos de backup em disco.

- Configuração persiste em backup_config.json (ativado, intervalo, retenção).
- Backups automáticos são gravados em backups/ com prefixo auto_backup_ e
  rotacionados conforme a retenção configurada.
- Um loop asyncio (iniciado no lifespan da aplicação) dispara o backup
  periodicamente quando habilitado.
"""
import os
import json
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from services.backup import generate_backup

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "backup_config.json"

DEFAULT_CONFIG = {
    "enabled": False,
    "interval_hours": 24,
    "retention": 7,
    "directory": "backups",
    "auto_prefix": "auto_backup_",
}

_AUTO_TASK = None
_STOP_EVENT = None


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
def load_backup_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    except Exception as e:
        logger.warning("[BACKUP] Falha ao ler config, usando padrões: %s", e)
    _sanitize_config(cfg)
    return cfg


def save_backup_config(new_cfg: dict) -> dict:
    cfg = load_backup_config()
    for key in ("enabled", "interval_hours", "retention", "directory", "auto_prefix"):
        if key in new_cfg:
            cfg[key] = new_cfg[key]
    _sanitize_config(cfg)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("[BACKUP] Falha ao salvar config: %s", e)
        raise
    return cfg


def _sanitize_config(cfg: dict) -> None:
    cfg["enabled"] = bool(cfg.get("enabled", False))
    try:
        cfg["interval_hours"] = max(1, min(int(cfg.get("interval_hours", 24)), 8760))
    except (TypeError, ValueError):
        cfg["interval_hours"] = 24
    try:
        cfg["retention"] = max(1, min(int(cfg.get("retention", 7)), 200))
    except (TypeError, ValueError):
        cfg["retention"] = 7
    cfg["directory"] = str(cfg.get("directory") or "backups")
    cfg["auto_prefix"] = str(cfg.get("auto_prefix") or "auto_backup_")


# --------------------------------------------------------------------------- #
# Gravação em disco
# --------------------------------------------------------------------------- #
def _backup_dir(cfg: dict) -> Path:
    d = Path(cfg["directory"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_backup_to_disk(prefix: str = "manual_backup_") -> dict:
    """Gera o backup e grava em disco. Retorna resumo do arquivo criado."""
    backup = generate_backup()
    cfg = load_backup_config()
    d = _backup_dir(cfg)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}{timestamp}.json"
    path = d / filename
    content = json.dumps(backup, ensure_ascii=False, indent=2, default=str)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    size = path.stat().st_size
    logger.info("[BACKUP] Backup salvo em disco: %s (%.1f KB)", path, size / 1024)
    return {"filename": filename, "path": str(path), "size": size, "registros": _contar_registros(backup)}


def run_auto_backup() -> dict:
    """Backup automático: grava com prefixo auto_ e aplica retenção."""
    cfg = load_backup_config()
    result = save_backup_to_disk(prefix=cfg["auto_prefix"])
    _prune_backups(cfg)
    result["retencao"] = cfg["retention"]
    return result


def _contar_registros(backup: dict) -> int:
    tables = backup.get("tables", {})
    if isinstance(tables, dict):
        return sum(len(v) for v in tables.values() if isinstance(v, list))
    return 0


def _prune_backups(cfg: dict) -> int:
    """Remove arquivos do prefixo automático além da retenção (mantém os mais novos)."""
    d = _backup_dir(cfg)
    prefix = cfg["auto_prefix"]
    try:
        files = [p for p in d.iterdir() if p.is_file() and p.name.startswith(prefix) and p.name.endswith(".json")]
    except Exception:
        return 0
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    removidos = 0
    for p in files[cfg["retention"]:]:
        try:
            p.unlink()
            removidos += 1
        except Exception as e:
            logger.warning("[BACKUP] Não foi possível remover %s: %s", p, e)
    if removidos:
        logger.info("[BACKUP] Retenção aplicada: %d arquivo(s) antigo(s) removido(s)", removidos)
    return removidos


def list_backup_files() -> list:
    """Lista arquivos de backup em disco (qualquer prefixo) para o usuário."""
    cfg = load_backup_config()
    d = _backup_dir(cfg)
    try:
        files = [p for p in d.iterdir() if p.is_file() and p.name.endswith(".json")]
    except Exception:
        return []
    items = []
    for p in files:
        mtime = p.stat().st_mtime
        tipo = "auto" if p.name.startswith(cfg["auto_prefix"]) else "manual"
        items.append({
            "nome": p.name,
            "size_kb": round(p.stat().st_size / 1024, 1),
            "modified": datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M:%S"),
            "tipo": tipo,
        })
    items.sort(key=lambda x: x["nome"], reverse=True)
    return items


def _resolve_backup_path(nome: str) -> Path:
    """Valida e resolve o caminho de um arquivo de backup (previne path traversal)."""
    if not nome or not nome.endswith(".json"):
        raise ValueError("Nome de arquivo inválido")
    if "/" in nome or "\\" in nome or ".." in nome:
        raise ValueError("Caminho de arquivo não permitido")
    cfg = load_backup_config()
    d = _backup_dir(cfg)
    path = (d / nome).resolve()
    if path.parent.resolve() != d.resolve() or not path.exists():
        raise ValueError("Arquivo não encontrado")
    return path


def read_backup_file(nome: str) -> tuple:
    path = _resolve_backup_path(nome)
    with open(path, "r", encoding="utf-8") as f:
        data = f.read()
    return data, path


def delete_backup_file(nome: str) -> bool:
    path = _resolve_backup_path(nome)
    path.unlink()
    return True


# --------------------------------------------------------------------------- #
# Scheduler (asyncio)
# --------------------------------------------------------------------------- #
async def _scheduler_loop():
    global _STOP_EVENT
    _STOP_EVENT = asyncio.Event()
    while not _STOP_EVENT.is_set():
        cfg = load_backup_config()
        if cfg["enabled"]:
            try:
                await asyncio.to_thread(run_auto_backup)
            except Exception as e:
                logger.error("[BACKUP] Falha no backup automático: %s", e)
        interval = max(1, int(cfg["interval_hours"])) * 3600
        try:
            await asyncio.wait_for(_STOP_EVENT.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _initial_backup():
    """Dispara um primeiro backup automático ~30s após subir, se habilitado."""
    await asyncio.sleep(30)
    cfg = load_backup_config()
    if not cfg["enabled"] or (_STOP_EVENT and _STOP_EVENT.is_set()):
        return
    try:
        await asyncio.to_thread(run_auto_backup)
    except Exception as e:
        logger.error("[BACKUP] Falha no backup inicial: %s", e)


def start_backup_scheduler():
    """Inicia o loop de agendamento. Idempotente."""
    global _AUTO_TASK
    if _AUTO_TASK is not None and not _AUTO_TASK.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    _AUTO_TASK = loop.create_task(_scheduler_loop())
    loop.create_task(_initial_backup())
    logger.info("[BACKUP] Scheduler de backup iniciado")


def stop_backup_scheduler():
    """Cancela o scheduler de forma limpa."""
    global _AUTO_TASK, _STOP_EVENT
    if _STOP_EVENT is not None:
        _STOP_EVENT.set()
    if _AUTO_TASK is not None:
        _AUTO_TASK.cancel()
        _AUTO_TASK = None
