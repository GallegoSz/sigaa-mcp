"""Teste rápido contra o SIGAA real. Uso: uv run python testar.py (com SIGAA_USER/SIGAA_PASS definidos)."""

import asyncio
import json
import os
from pathlib import Path

from sigaa_mcp import server
from sigaa_mcp.client import SigaaClient


def mostrar(titulo: str, dados: object) -> None:
    print(f"\n===== {titulo} =====")
    print(json.dumps(dados, ensure_ascii=False, indent=2))


def carregar_env(caminho: Path = Path(__file__).with_name(".env")) -> None:
    """Lê SIGAA_USER/SIGAA_PASS de um .env local (ignorado pelo git), se existir."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        chave, sep, valor = linha.partition("=")
        if sep and not linha.lstrip().startswith("#"):
            os.environ.setdefault(chave.strip(), valor.strip())


async def main() -> None:
    carregar_env()
    client = SigaaClient(os.environ["SIGAA_USER"], os.environ["SIGAA_PASS"])
    server._client = client
    try:
        mostrar("calendario_academico", await server.get_calendario_academico())
        mostrar("avaliacoes", await server.get_avaliacoes())
        conteudo = await server.get_conteudo("calculo")
        mostrar("conteudo (cálculo, 3 primeiros tópicos)", [
            {**t, "dados": t["dados"][:3]} for t in conteudo
        ])
        mostrar("situacao_notas", await server.get_situacao_notas())
        mostrar("alertas_frequencia", await server.get_alertas_frequencia())
        mostrar("resumo_semana", await server.get_resumo_semana())
    finally:
        await client.aclose()


asyncio.run(main())
