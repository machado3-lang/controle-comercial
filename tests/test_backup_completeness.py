"""Testes de completude do backup lógico (services/backup.py).

Cobrem:
- descoberta automática de tabelas (nenhuma tabela fica de fora);
- serialização de Decimal como string (sem perda de precisão);
- metadados de versão de schema;
- validação de cobertura antes/depois do restore;
- ida e volta (dump -> limpar -> restore) em SQLite.
"""
import pytest
from sqlalchemy import text

from database import engine
from services.backup import (
    IGNORED_TABLES,
    generate_backup,
    list_database_tables,
    restore_backup,
    validate_backup,
)


def _exec(sql, params=None):
    with engine.connect() as conn:
        conn.execute(text(sql), params or {})
        conn.commit()


def _fetch(sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).fetchall()


# --------------------------------------------------------------------------- #
# Geração
# --------------------------------------------------------------------------- #
def test_generate_backup_inclui_todas_as_tabelas_do_banco(test_engine):
    backup = generate_backup()
    esperadas = set(list_database_tables())

    assert backup["version"] == 2
    assert set(backup["tables"].keys()) == esperadas
    assert IGNORED_TABLES.isdisjoint(backup["tables"].keys())
    assert backup["table_order"] == list(backup["tables"].keys())


def test_generate_backup_inclui_tabela_desconhecida(test_engine):
    """Tabela criada por migração/rotina externa não pode ficar de fora."""
    _exec(
        "CREATE TABLE tabela_nova_backup "
        "(id INTEGER PRIMARY KEY, nome TEXT)"
    )
    try:
        _exec("INSERT INTO tabela_nova_backup (id, nome) VALUES (1, 'a')")

        backup = generate_backup()

        assert backup["tables"]["tabela_nova_backup"] == [{"id": 1, "nome": "a"}]
        assert "tabela_nova_backup" in backup["table_order"]
        assert any("tabela_nova_backup" in w for w in backup["warnings"])
    finally:
        _exec("DROP TABLE tabela_nova_backup")


def test_decimal_serializado_como_string_sem_perda(test_engine):
    _exec(
        "CREATE TABLE tabela_decimal_backup "
        "(id INTEGER PRIMARY KEY, valor NUMERIC(12,2))"
    )
    try:
        _exec("INSERT INTO tabela_decimal_backup (id, valor) VALUES (1, 273.61)")

        backup = generate_backup()

        row = backup["tables"]["tabela_decimal_backup"][0]
        assert row["valor"] == "273.61"
        assert isinstance(row["valor"], str)
    finally:
        _exec("DROP TABLE tabela_decimal_backup")


def test_generate_backup_emite_schema_version(test_engine):
    backup = generate_backup()
    # No banco de teste não há alembic_version; a chave precisa existir.
    assert "schema_version" in backup


# --------------------------------------------------------------------------- #
# Validação
# --------------------------------------------------------------------------- #
def test_validate_backup_ok_para_backup_completo(test_engine):
    backup = generate_backup()

    relatorio = validate_backup(backup)

    assert relatorio["ok"] is True, relatorio["avisos"]
    assert relatorio["tabelas_no_backup"] == relatorio["tabelas_no_banco"]
    assert relatorio["tabelas_ausentes_no_banco"] == []
    assert relatorio["tabelas_no_banco_fora_do_backup"] == []


def test_validate_backup_aponta_tabela_ausente_no_destino(test_engine):
    backup = generate_backup()
    backup["tables"]["tabela_fantasma"] = [{"id": 1, "nome": "x"}]
    backup["table_order"].append("tabela_fantasma")

    relatorio = validate_backup(backup)

    assert relatorio["ok"] is False
    assert relatorio["tabelas_ausentes_no_banco"] == ["tabela_fantasma"]
    assert any("tabela_fantasma" in aviso for aviso in relatorio["avisos"])


def test_validate_backup_aponta_coluna_ausente_no_destino(test_engine):
    backup = generate_backup()
    backup["tables"]["clientes"] = [
        {"id": 1, "nome": "a", "coluna_que_nao_existe": 1}
    ]

    relatorio = validate_backup(backup)

    assert relatorio["ok"] is False
    assert "coluna_que_nao_existe" in relatorio["colunas_ausentes_no_banco"]["clientes"]


def test_validate_backup_aceita_formato_legado(test_engine):
    """Backups antigos (dict raiz = {tabela: [linhas]}) continuam válidos."""
    legado = {"marcas_produto": [{"id": 1, "nome": "Marca"}]}

    relatorio = validate_backup(legado)

    assert relatorio["tabelas_no_backup"] == 1
    # O backup legado não cobre as demais tabelas: precisa avisar, não explodir.
    assert "marcas_produto" in relatorio["colunas_no_banco_ausentes_no_backup"]
    assert relatorio["ok"] is False


# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #
def test_restore_ignora_tabela_inexistente_no_destino(test_engine):
    backup = {
        "version": 2,
        "tables": {"tabela_fantasma": [{"id": 1}]},
        "table_order": ["tabela_fantasma"],
    }

    stats = restore_backup(backup, modo="sobrepor")

    assert stats["imported"] == 0
    assert stats["tabelas_ausentes_no_banco"] == ["tabela_fantasma"]
    assert stats["tabelas"]["tabela_fantasma"]["ignorado"] == 1
    assert any("tabela_fantasma" in d for d in stats["detalhes"])


def test_restore_rejeita_nome_de_tabela_invalido(test_engine):
    with pytest.raises(ValueError):
        restore_backup(
            {"tables": {"x; DROP TABLE clientes": [{"id": 1}]}},
            modo="sobrepor",
        )


def test_restore_roundtrip_modo_limpar(test_engine):
    _exec(
        "CREATE TABLE tabela_roundtrip "
        "(id INTEGER PRIMARY KEY, valor NUMERIC(12,2), nome TEXT)"
    )
    try:
        _exec(
            "INSERT INTO tabela_roundtrip (id, valor, nome) "
            "VALUES (1, 10.55, 'um'), (2, 20.25, 'dois')"
        )
        backup = generate_backup()

        # Estado divergente: linha extra e linha apagada.
        _exec("DELETE FROM tabela_roundtrip WHERE id = 2")
        _exec(
            "INSERT INTO tabela_roundtrip (id, valor, nome) "
            "VALUES (99, 1.00, 'antigo')"
        )

        stats = restore_backup(backup, modo="limpar")

        assert stats["erros"] == 0, stats["detalhes"]
        rows = _fetch(
            "SELECT id, valor, nome FROM tabela_roundtrip ORDER BY id"
        )
        assert [(r[0], str(r[1]), r[2]) for r in rows] == [
            (1, "10.55", "um"),
            (2, "20.25", "dois"),
        ]
    finally:
        _exec("DROP TABLE tabela_roundtrip")


def test_restore_preserva_precisao_decimal(test_engine):
    _exec(
        "CREATE TABLE tabela_precisao "
        "(id INTEGER PRIMARY KEY, valor NUMERIC(12,2))"
    )
    try:
        _exec(
            "INSERT INTO tabela_precisao (id, valor) "
            "VALUES (1, 1234.56), (2, 0.99), (3, 273.61)"
        )
        backup = generate_backup()
        _exec("DELETE FROM tabela_precisao")

        stats = restore_backup(backup, modo="limpar")

        assert stats["erros"] == 0, stats["detalhes"]
        rows = _fetch("SELECT valor FROM tabela_precisao ORDER BY id")
        assert [str(r[0]) for r in rows] == ["1234.56", "0.99", "273.61"]
    finally:
        _exec("DROP TABLE tabela_precisao")
