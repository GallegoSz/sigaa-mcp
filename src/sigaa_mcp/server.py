"""Servidor MCP do SIGAA."""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from . import analise
from .client import SigaaClient, SigaaError

mcp = MCPServer("sigaa")
_client: SigaaClient | None = None


def _get_client() -> SigaaClient:
    global _client
    if _client is None:
        usuario = os.environ.get("SIGAA_USER")
        senha = os.environ.get("SIGAA_PASS")
        if not usuario or not senha:
            raise SigaaError("Defina as variáveis de ambiente SIGAA_USER e SIGAA_PASS")
        _client = SigaaClient(usuario, senha, os.environ.get("SIGAA_URL", "https://sigaa.unemat.br"))
    return _client


@mcp.tool()
async def get_notas(semestre: str | None = None) -> list[dict]:
    """Notas do aluno por semestre (unidades, recuperação, prova final, resultado, faltas, situação).

    'faltas' só vem preenchido em disciplinas já consolidadas (semestres anteriores);
    para o semestre em andamento use get_frequencia.

    Args:
        semestre: filtra por um semestre, no formato '2026.2'. Se omitido, retorna todos.
    """
    semestres = await _get_client().notas()
    if semestre:
        semestres = [s for s in semestres if s["semestre"] == semestre]
    return semestres


@mcp.tool()
async def get_turmas() -> list[dict]:
    """Turmas em que o aluno está matriculado no semestre atual (componente, local, horário)."""
    return await _get_client().turmas()


@mcp.tool()
async def get_dados_discente() -> dict[str, str]:
    """Dados institucionais e índices do aluno (matrícula, curso, status, CRA, carga horária pendente, % integralizado)."""
    return await _get_client().dados_discente()


@mcp.tool()
async def get_agenda() -> list[dict]:
    """Tarefas e avaliações com data (bloco 'Minhas atividades' do portal), em ordem cronológica.

    Inclui itens recentes já vencidos; use 'dias_restantes' (negativo = passou) para filtrar.
    """
    return await _get_client().agenda()


@mcp.tool()
async def get_frequencia(disciplina: str | None = None) -> list[dict]:
    """Frequência por aula, total de faltas e percentuais de presença de cada turma.

    Mais confiável que o campo 'faltas' de get_notas. Consulta cada turma virtual,
    então sem filtro pode levar alguns segundos.

    Args:
        disciplina: parte do nome da disciplina (sem diferenciar acentos/caixa). Se omitido, todas.
    """
    return await _get_client().frequencia(disciplina)


@mcp.tool()
async def get_tarefas(disciplina: str | None = None) -> list[dict]:
    """Tarefas da turma virtual: título, período de entrega, se vale nota, se já foi enviada e enunciado.

    Args:
        disciplina: parte do nome da disciplina (sem diferenciar acentos/caixa). Se omitido, todas.
    """
    return await _get_client().tarefas(disciplina)


@mcp.tool()
async def get_noticias(disciplina: str | None = None) -> list[dict]:
    """Notícias publicadas pelos professores na turma virtual (título e data).

    Args:
        disciplina: parte do nome da disciplina (sem diferenciar acentos/caixa). Se omitido, todas.
    """
    return await _get_client().noticias(disciplina)


@mcp.tool()
async def get_avaliacoes(disciplina: str | None = None) -> list[dict]:
    """Datas e horários das avaliações (provas) marcadas pelo professor em cada turma virtual.

    Args:
        disciplina: parte do nome da disciplina (sem diferenciar acentos/caixa). Se omitido, todas.
    """
    return await _get_client().avaliacoes(disciplina)


@mcp.tool()
async def get_conteudo(disciplina: str | None = None) -> list[dict]:
    """Tópicos de aula da turma virtual (cronograma com datas, descrição e materiais: arquivos, vídeos, tarefas).

    Inclui tópicos futuros já planejados pelo professor. A saída pode ser grande; prefira filtrar.

    Args:
        disciplina: parte do nome da disciplina (sem diferenciar acentos/caixa). Se omitido, todas.
    """
    return await _get_client().conteudo(disciplina)


@mcp.tool()
async def get_calendario_academico(semestre: str | None = None) -> dict:
    """Calendário acadêmico do curso: período letivo, consolidação, trancamento, matrícula, etc.

    Args:
        semestre: no formato '2026.2'. Se omitido, usa o semestre atual do portal.
    """
    return await _get_client().calendario(semestre)


@mcp.tool()
async def get_situacao_notas() -> list[dict]:
    """Quanto precisa tirar nas unidades restantes de cada disciplina do semestre atual.

    Regras da UNEMAT (Resolução 001/2024-CONEPE, Art. 61): média >= 6,0 aprova; média entre
    4,0 e 5,9 (com frequência >= 75%) vai para exame final, onde precisa de 5,0. Considera
    a média aritmética das unidades; o plano de curso da disciplina pode usar pesos diferentes.
    """
    semestres = await _get_client().notas()
    if not semestres:
        return []
    return [
        analise.situacao_notas(d)
        for d in semestres[0]["disciplinas"]
        if d["situacao"] == "MATRICULADO"
    ]


@mcp.tool()
async def get_alertas_frequencia() -> list[dict]:
    """Quantas faltas ainda cabem em cada disciplina antes de reprovar por frequência.

    Regra da UNEMAT (Resolução 001/2024-CONEPE, Art. 43): presença mínima de 75% da carga
    horária presencial; abaixo disso reprova independentemente da nota. Faltas não são abonadas.
    """
    return [
        analise.alerta_frequencia(t["disciplina"], t["dados"])
        for t in await _get_client().frequencia()
    ]


@mcp.tool()
async def get_resumo_semana(dias: int = 7) -> dict:
    """Resumo dos próximos dias: agenda (provas/tarefas), tarefas abertas ainda não enviadas,
    notícias recentes das turmas e alertas de frequência.

    Args:
        dias: tamanho da janela em dias (padrão 7).
    """
    dados = await _get_client().dados_semana()
    alertas = [
        analise.alerta_frequencia(t["disciplina"], t["dados"]) for t in dados["frequencia"]
    ]
    return analise.resumo_semana(
        dados["agenda"], dados["tarefas"], dados["noticias"], alertas, dias=dias
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
