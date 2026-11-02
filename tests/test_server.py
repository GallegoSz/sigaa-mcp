"""Tools MCP (server.py): registro, schema e a lógica que fica no próprio servidor."""

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from sigaa_mcp import server
from sigaa_mcp.client import SigaaError

TOOLS = {
    "get_notas", "get_turmas", "get_dados_discente", "get_agenda", "get_frequencia",
    "get_tarefas", "get_noticias", "get_avaliacoes", "get_conteudo", "get_calendario_academico",
    "get_situacao_notas", "get_alertas_frequencia", "get_resumo_semana",
}


def _disc(nome, situacao, *notas):
    return {"disciplina": nome, "situacao": situacao,
            "unidades": {f"Unid. {i}": n for i, n in enumerate(notas, 1)}}


class FakeClient:
    async def notas(self):
        return [
            {"semestre": "2026.2", "disciplinas": [
                _disc("CÁLCULO I", "MATRICULADO", 7.0, None),
                _disc("ESTÁGIO", "APROVADO", 9.0, 9.0),
            ]},
            {"semestre": "2026.1", "disciplinas": [_disc("FÍSICA I", "APROVADO", 8.0, 8.0)]},
        ]

    async def frequencia(self, disciplina=None):
        return [{"disciplina": "CÁLCULO I",
                 "dados": {"aulas": [], "total_faltas": 0, "aulas_carga_horaria": 72}}]

    async def dados_semana(self):
        freq = await self.frequencia()
        return {"agenda": [], "tarefas": [], "noticias": [], "frequencia": freq}


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(server, "_client", FakeClient())


def test_todas_as_tools_registradas_com_descricao():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == TOOLS
    assert all(t.description for t in tools)


def test_parametros_opcionais_no_schema():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    schema = tools["get_frequencia"].input_schema
    assert "disciplina" in schema["properties"]
    assert "disciplina" not in schema.get("required", [])
    assert tools["get_resumo_semana"].input_schema["properties"]["dias"]["default"] == 7


def test_sem_credenciais_gera_erro_claro(monkeypatch):
    monkeypatch.setattr(server, "_client", None)
    monkeypatch.delenv("SIGAA_USER", raising=False)
    monkeypatch.delenv("SIGAA_PASS", raising=False)
    with pytest.raises(SigaaError, match="SIGAA_USER e SIGAA_PASS"):
        server._get_client()


def test_cliente_e_criado_uma_vez(monkeypatch):
    monkeypatch.setattr(server, "_client", None)
    monkeypatch.setenv("SIGAA_USER", "u")
    monkeypatch.setenv("SIGAA_PASS", "p")
    monkeypatch.setenv("SIGAA_URL", "https://sigaa.outra.br")
    c = server._get_client()
    try:
        assert server._get_client() is c
        assert str(c._http.base_url) == "https://sigaa.outra.br"
    finally:
        asyncio.run(c.aclose())


def test_get_notas_filtra_semestre(fake):
    assert [s["semestre"] for s in asyncio.run(server.get_notas())] == ["2026.2", "2026.1"]
    assert [s["semestre"] for s in asyncio.run(server.get_notas("2026.1"))] == ["2026.1"]


def test_get_situacao_notas_so_disciplinas_em_andamento_do_semestre_atual(fake):
    r = asyncio.run(server.get_situacao_notas())
    assert [d["disciplina"] for d in r] == ["CÁLCULO I"]
    assert r[0]["media_necessaria_restantes_para_aprovar"] == 5.0


def test_get_alertas_frequencia(fake):
    r = asyncio.run(server.get_alertas_frequencia())
    assert r[0]["limite_faltas"] == 18 and r[0]["status"] == "ok"


def test_get_resumo_semana_omite_alertas_ok(fake):
    r = asyncio.run(server.get_resumo_semana(dias=3))
    assert r["alertas_frequencia"] == []
    assert set(r) == {"periodo", "agenda", "tarefas_pendentes", "noticias_recentes", "alertas_frequencia"}


def test_erro_do_cliente_chega_como_tool_error(monkeypatch):
    class ClienteQueFalha:
        async def turmas(self):
            raise SigaaError("Falha no login: verifique SIGAA_USER e SIGAA_PASS")

    monkeypatch.setattr(server, "_client", ClienteQueFalha())
    # O SDK reembala o SigaaError (um ToolError), mantendo a mensagem que a IA vai ler.
    with pytest.raises(ToolError, match="get_turmas: Falha no login"):
        asyncio.run(server.mcp.call_tool("get_turmas", {}))
