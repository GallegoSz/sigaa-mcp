"""Cliente HTTP para o SIGAA (sessão por cookie + JSF)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urljoin

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from . import parser

BASE_URL = "https://sigaa.unemat.br"
PORTAL_DISCENTE = "/sigaa/portais/discente/discente.jsf"
CALENDARIO = "/sigaa/administracao/calendario_academico/consulta.jsf"
CACHE_TTL = 120  # segundos

# O httpx loga cada requisição em INFO; com ~20 por varredura de turmas isso polui o log do MCP.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Itens da turma virtual: chave -> (texto do link no menu lateral, parser).
# None = página principal da turma (aberta ao entrar).
_ITENS_TURMA = {
    "conteudo": (None, parser.parse_conteudo),
    "frequencia": ("Frequência", parser.parse_frequencia),
    "tarefas": ("Tarefas", parser.parse_tarefas),
    "noticias": ("Notícias", parser.parse_noticias),
    "avaliacoes": ("Avaliações", parser.parse_avaliacoes),
}

_META_CHARSET_RE = re.compile(rb'charset=["\']?([\w-]+)', re.I)


class SigaaError(ToolError):
    """Falha prevista; como ToolError, a mensagem chega à IA (outras exceções viram um erro genérico)."""


class SigaaClient:
    def __init__(self, usuario: str, senha: str, base_url: str = BASE_URL):
        self._usuario = usuario
        self._senha = senha
        self._http = httpx.AsyncClient(
            base_url=base_url,
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (sigaa-mcp)"},
        )
        self._lock = asyncio.Lock()
        self._logado = False
        self._cache: dict[tuple, tuple[float, object]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _html(resp: httpx.Response) -> str:
        # O SIGAA nem sempre declara o charset no header; nesse caso usa o <meta>.
        if resp.charset_encoding:
            return resp.text
        m = _META_CHARSET_RE.search(resp.content[:2048])
        return resp.content.decode(m.group(1).decode() if m else "iso-8859-1", "replace")

    async def _login(self) -> None:
        self._http.cookies.clear()
        await self._http.get("/sigaa/verTelaLogin.do")
        resp = await self._http.post(
            "/sigaa/logar.do",
            params={"dispatch": "logOn"},
            data={
                "width": "1536",
                "height": "865",
                "urlRedirect": "",
                "subsistemaRedirect": "",
                "acao": "",
                "acessibilidade": "",
                "user.login": self._usuario,
                "user.senha": self._senha,
            },
        )
        if parser.is_login_page(self._html(resp)):
            raise SigaaError("Falha no login: verifique SIGAA_USER e SIGAA_PASS")
        self._logado = True

    async def _portal(self) -> str:
        """HTML do portal do discente, logando (de novo) se necessário."""
        if not self._logado:
            await self._login()
        resp = await self._http.get(PORTAL_DISCENTE)
        html = self._html(resp)
        if parser.is_login_page(html):
            # Sessão expirou: loga de novo uma única vez.
            await self._login()
            html = self._html(await self._http.get(PORTAL_DISCENTE))
        if "menu:form_menu_discente" not in html:
            raise SigaaError(
                "Portal do discente não carregou (talvez haja uma tela intermediária "
                "pendente no SIGAA; acesse pelo navegador para resolvê-la)"
            )
        return html

    async def _submit(self, url_atual: str, action: str, campos: dict[str, str]) -> str:
        resp = await self._http.post(urljoin(url_atual, action or url_atual), data=campos)
        html = self._html(resp)
        if parser.is_login_page(html):
            self._logado = False
            raise SigaaError("Sessão expirou durante a requisição; tente novamente")
        return html

    async def _clicar(
        self,
        html: str,
        url_atual: str,
        form_id: str,
        rotulo: str | None = None,
        link_id: str | None = None,
    ) -> str:
        """Simula o clique num commandLink JSF (h:commandLink), achado pelo texto ou id."""
        action, campos = parser.extract_form(html, form_id)
        campos.update(parser.extract_link_params(html, form_id, rotulo, link_id))
        return await self._submit(url_atual, action, campos)

    async def _acao_menu(self, rotulo: str, portal: str | None = None) -> str:
        portal = portal or await self._portal()
        action, campos = parser.extract_form(portal, "menu:form_menu_discente")
        campos["jscook_action"] = parser.extract_menu_action(portal, rotulo)
        return await self._submit(PORTAL_DISCENTE, action, campos)

    def _cache_get(self, chave: tuple) -> object | None:
        item = self._cache.get(chave)
        if item and time.monotonic() - item[0] < CACHE_TTL:
            return item[1]
        return None

    async def _varrer_turmas(
        self, itens: list[str], disciplina: str | None
    ) -> dict[str, list[dict]]:
        """Coleta itens da turma virtual (ver _ITENS_TURMA) entrando uma vez em cada turma."""
        portal: str | None = await self._portal()
        turmas = parser.extract_turmas_virtuais(portal)
        if disciplina:
            alvo = parser.normalizar(disciplina)
            filtradas = [t for t in turmas if alvo in parser.normalizar(t[1])]
            if not filtradas:
                # Lista os nomes para quem chamou (geralmente a IA) corrigir o filtro de primeira.
                nomes = "; ".join(nome for _, nome in turmas)
                raise SigaaError(
                    f"Nenhuma turma corresponde a {disciplina!r}. Turmas disponíveis: {nomes}"
                )
            turmas = filtradas
        for form_id, nome in turmas:
            faltando = [it for it in itens if self._cache_get((nome, it)) is None]
            if not faltando:
                continue
            # O ViewState muda a cada requisição, então o portal é recarregado por turma.
            portal = portal or await self._portal()
            pagina_turma = await self._clicar(portal, PORTAL_DISCENTE, form_id, rotulo=nome)
            portal = None
            pagina = pagina_turma
            for item in faltando:
                rotulo, parse = _ITENS_TURMA[item]
                if rotulo is None:
                    html = pagina_turma
                else:
                    html = pagina = await self._clicar(pagina, PORTAL_DISCENTE, "formMenu", rotulo)
                self._cache[(nome, item)] = (time.monotonic(), parse(html))
        return {
            item: [
                {"disciplina": nome, "dados": self._cache[(nome, item)][1]}
                for _, nome in turmas
            ]
            for item in itens
        }

    async def _por_turma(self, item: str, disciplina: str | None) -> list[dict]:
        async with self._lock:
            return (await self._varrer_turmas([item], disciplina))[item]

    async def notas(self) -> list[dict]:
        async with self._lock:
            return parser.parse_notas(await self._acao_menu("Consultar Minhas Notas"))

    async def turmas(self) -> list[dict]:
        async with self._lock:
            return parser.parse_turmas(await self._portal())

    async def dados_discente(self) -> dict[str, str]:
        async with self._lock:
            return parser.parse_dados_discente(await self._portal())

    async def agenda(self) -> list[dict]:
        async with self._lock:
            return parser.parse_agenda(await self._portal())

    async def frequencia(self, disciplina: str | None = None) -> list[dict]:
        return await self._por_turma("frequencia", disciplina)

    async def tarefas(self, disciplina: str | None = None) -> list[dict]:
        return await self._por_turma("tarefas", disciplina)

    async def noticias(self, disciplina: str | None = None) -> list[dict]:
        return await self._por_turma("noticias", disciplina)

    async def avaliacoes(self, disciplina: str | None = None) -> list[dict]:
        return await self._por_turma("avaliacoes", disciplina)

    async def conteudo(self, disciplina: str | None = None) -> list[dict]:
        return await self._por_turma("conteudo", disciplina)

    async def dados_semana(self) -> dict:
        """Agenda do portal + tarefas, notícias e frequência de todas as turmas."""
        async with self._lock:
            agenda = parser.parse_agenda(await self._portal())
            dados = await self._varrer_turmas(["tarefas", "noticias", "frequencia"], None)
        return {"agenda": agenda, **dados}

    async def calendario(self, semestre: str | None = None) -> dict:
        async with self._lock:
            portal = await self._portal()
            semestre = semestre or parser.parse_semestre_atual(portal)
            curso = parser.normalizar(parser.parse_dados_discente(portal).get("Curso", ""))
            busca = await self._acao_menu("Calendário Acadêmico", portal)

            # Filtra pela modalidade presencial e pelo convênio que aparece no nome do curso
            # (ex.: "... - CONTÍNUA - N"); sem convênio o SIGAA só lista calendários antigos.
            action, campos = parser.extract_form(busca, "form")
            for valor, texto in parser.extract_select_options(busca, "form:modalidade"):
                if parser.normalizar(texto) == "PRESENCIAL":
                    campos["form:modalidade"] = valor
            convenio = ""
            for valor, texto in parser.extract_select_options(busca, "form:convenio"):
                if valor != "0" and f"- {parser.normalizar(texto)} " in f"{curso} ":
                    campos["form:convenio"], convenio = valor, parser.normalizar(texto)
            campos["form:buscar"] = "Buscar"
            lista_html = await self._submit(CALENDARIO, action, campos)
            calendarios = parser.parse_lista_calendarios(lista_html)

            candidatos = [c for c in calendarios if c["semestre"] == semestre]
            if not candidatos:
                disponiveis = sorted({c["semestre"] for c in calendarios}, reverse=True)
                raise SigaaError(
                    f"Calendário de {semestre} não encontrado. Disponíveis: {disponiveis[:10]}"
                )
            escolhido = next(
                (c for c in candidatos if convenio and convenio in parser.normalizar(c["nome"])),
                candidatos[0],
            )
            action, campos = parser.extract_form(lista_html, "form")
            campos.update(escolhido["params"])
            pagina = await self._submit(CALENDARIO, action, campos)
            return {
                "semestre": semestre,
                "calendario": escolhido["nome"],
                **parser.parse_calendario(pagina),
            }
