"""SigaaClient contra um SIGAA falso (httpx.MockTransport): login, sessão, cliques JSF e cache."""

import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from sigaa_mcp import client as client_mod
from sigaa_mcp.client import CALENDARIO, PORTAL_DISCENTE, SigaaClient, SigaaError

USUARIO, SENHA = "aluno", "certa"

LOGIN = '<html><form><input name="user.login"><input type="password" name="user.senha"></form></html>'


def _link(form_id: str, texto: str, **params: str) -> str:
    pares = ",".join(f"'{k}':'{v}'" for k, v in params.items())
    return (
        f"<a onclick=\"jsfcljs(document.getElementById('{form_id}'),{{{pares}}},'');return false\">"
        f"{texto}</a>"
    )


def _turma(n: int, nome: str) -> str:
    form_id = f"form_acessarTurmaVirtual{n}"
    return (
        f'<form id="{form_id}" action="{PORTAL_DISCENTE}">'
        f'<input type="hidden" name="javax.faces.ViewState" value="__VS__">'
        + _link(form_id, nome, idTurma=str(n))
        + "</form>"
    )


PORTAL = f"""<html><body>
<div>Semestre atual: 2026.2</div>
<table><tr><td>Curso:</td><td>CIÊNCIA DA COMPUTAÇÃO - CONTÍNUA - N</td></tr></table>
<form id="menu:form_menu_discente" action="{PORTAL_DISCENTE}">
  <input type="hidden" name="javax.faces.ViewState" value="__VS__">
  <input type="hidden" name="jscook_action" value="">
</form>
<script>var menu = [
  [null, 'Consultar Minhas Notas', 'menu_form_menu_discente_notas', 'menu:form_menu_discente', null],
  [null, 'Calend&aacute;rio Acad&ecirc;mico', 'menu_form_menu_discente_calendario', 'menu:form_menu_discente', null]
];</script>
{_turma(1, "CÁLCULO I")}
{_turma(2, "FÍSICA I")}
<div id="avaliacao-portal"><table>
  <tr><td></td><td>30/10/2026 23:59</td><td><small>CÁLCULO I<br>Tarefa: Lista 3</small></td></tr>
</table></div>
</body></html>"""

NOTAS = """<table class="tabelaRelatorio"><caption>2026.2</caption>
<thead><tr><th>Código</th><th>Disciplina</th><th>Unid. 1</th><th>Situação</th></tr></thead>
<tbody><tr><td>MAT01</td><td>CÁLCULO I</td><td>7,0</td><td>MATRICULADO</td></tr></tbody>
</table>"""

BUSCA_CALENDARIO = """<form id="form" action="/sigaa/administracao/calendario_academico/consulta.jsf">
<input type="hidden" name="javax.faces.ViewState" value="__VS__">
<select name="form:modalidade"><option value="0">-- SELECIONE --</option>
  <option value="1">Presencial</option><option value="2">A Distância</option></select>
<select name="form:convenio"><option value="0">-- SELECIONE --</option>
  <option value="5">Contínua</option><option value="6">Parcelada</option></select>
</form>"""

LISTA_CALENDARIOS = """<form id="form" action="/sigaa/administracao/calendario_academico/consulta.jsf">
<input type="hidden" name="javax.faces.ViewState" value="__VS__"><table>
<tr class="secao"><td>2026.2</td></tr>
<tr><td>CALENDÁRIO GERAL 2026.2</td><td><a id="form:visualizar1" onclick="jsfcljs(f,{'id':'1'},'')">v</a></td></tr>
<tr><td>CALENDÁRIO 2026.2 - CONTÍNUA</td><td><a id="form:visualizar2" onclick="jsfcljs(f,{'id':'2'},'')">v</a></td></tr>
<tr class="secao"><td>2026.1</td></tr>
<tr><td>CALENDÁRIO 2026.1</td><td><a id="form:visualizar3" onclick="jsfcljs(f,{'id':'3'},'')">v</a></td></tr>
</table></form>"""

ITENS_MENU_TURMA = {"Frequência": "frequencia", "Tarefas": "tarefas", "Notícias": "noticias"}


def _menu_turma() -> str:
    """Menu lateral da turma virtual, presente em todas as páginas dela."""
    links = "".join(_link("formMenu", rotulo, item=item) for rotulo, item in ITENS_MENU_TURMA.items())
    return f"""<form id="formMenu" action="/sigaa/ava/index.jsf">
  <input type="hidden" name="javax.faces.ViewState" value="__VS__">{links}
</form>"""


def _pagina_turma(nome: str) -> str:
    return f"""<html><body>{_menu_turma()}
<div class="topico-aula"><div class="titulo">Aula de {nome} (01/10/2026)</div></div>
</body></html>"""


def _pagina_item(nome: str, item: str) -> str:
    if item == "frequencia":
        faltas = "4 Faltas" if nome == "CÁLCULO I" else "Presente"
        corpo = f"""<table class="listing"><tr><th>Data</th><th>Situação</th></tr><tbody>
            <tr><td>01/10/2026</td><td>{faltas}</td></tr></tbody></table>
            <p>Aulas definidas pela CH do Componente: 72</p>"""
    elif item == "tarefas":
        corpo = f"""<table class="listing"><tbody><tr><td></td><td>Tarefa de {nome}</td>
            <td>De 01/10/2026 às 08h00 até 30/10/2026 às 23h59</td><td>Sim</td><td>0</td>
            <td><a title="Enviar Tarefa">e</a></td></tr></tbody></table>"""
    else:
        corpo = f'<table class="listing"><tr><td>Aviso de {nome}</td><td>27/10/2026</td></tr></table>'
    return f"<html><body>{_menu_turma()}{corpo}</body></html>"


class FakeSigaa:
    """Simula o servidor: guarda a sessão e registra as requisições recebidas.

    Como no JSF real, cada página sai com um ViewState novo e um POST com um ViewState
    que não seja o da última página entregue é rejeitado.
    """

    def __init__(self):
        self.logado = False
        self.intermediaria = False
        self.turma_atual: str | None = None
        self.logins = 0
        self.viewstate = 0
        self.requisicoes: list[tuple[str, str, dict[str, str]]] = []

    def entradas_em_turma(self) -> list[str]:
        return [d["idTurma"] for _, _, d in self.requisicoes if "idTurma" in d]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        # Cede a vez ao event loop, como uma requisição de rede de verdade.
        await asyncio.sleep(0)
        dados = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requisicoes.append((request.method, request.url.path, dados))
        html = self._responder(request, dados)
        if "__VS__" in html:
            self.viewstate += 1
            html = html.replace("__VS__", f"vs{self.viewstate}")
        return httpx.Response(200, html=html)

    def _responder(self, request: httpx.Request, dados: dict[str, str]) -> str:
        path = request.url.path
        if path == "/sigaa/verTelaLogin.do":
            return LOGIN
        if path == "/sigaa/logar.do":
            self.logins += 1
            self.logado = dados.get("user.login") == USUARIO and dados.get("user.senha") == SENHA
            return "<html>bem-vindo</html>" if self.logado else LOGIN
        if not self.logado:
            return LOGIN
        if request.method == "GET" and path == PORTAL_DISCENTE:
            return "<html>Questionário obrigatório</html>" if self.intermediaria else PORTAL
        # Todo clique JSF precisa reenviar o ViewState da última página recebida.
        enviado = dados.get("javax.faces.ViewState")
        if enviado != f"vs{self.viewstate}":
            raise AssertionError(f"ViewState velho: {enviado!r} (atual: vs{self.viewstate}) em {path}")
        acao = dados.get("jscook_action")
        if acao == "menu_form_menu_discente_notas":
            return NOTAS
        if acao == "menu_form_menu_discente_calendario":
            return BUSCA_CALENDARIO
        if "idTurma" in dados:
            self.turma_atual = {"1": "CÁLCULO I", "2": "FÍSICA I"}[dados["idTurma"]]
            return _pagina_turma(self.turma_atual)
        if "item" in dados:
            return _pagina_item(self.turma_atual, dados["item"])
        if path == CALENDARIO and dados.get("form:buscar"):
            return LISTA_CALENDARIOS
        if path == CALENDARIO and "id" in dados:
            return f"""<table class="visualizacao"><tr><th>Calendário:</th><td>id {dados['id']}</td></tr>
                <tr><th>Período letivo:</th><td>01/08/2026 a 15/12/2026</td></tr></table>"""
        raise AssertionError(f"Requisição inesperada: {request.method} {path} {dados}")


@pytest.fixture
def sigaa():
    return FakeSigaa()


@pytest.fixture
def cliente(sigaa):
    c = SigaaClient(USUARIO, SENHA)
    c._http = httpx.AsyncClient(
        base_url="https://sigaa.teste", transport=httpx.MockTransport(sigaa), follow_redirects=True
    )
    return c


def rodar(coro):
    return asyncio.run(coro)


# --- Login e sessão -----------------------------------------------------------


def test_login_com_senha_errada(sigaa):
    c = SigaaClient(USUARIO, "errada")
    c._http = httpx.AsyncClient(base_url="https://sigaa.teste", transport=httpx.MockTransport(sigaa))
    with pytest.raises(SigaaError, match="Falha no login"):
        rodar(c.turmas())


def test_login_acontece_uma_vez_e_e_reaproveitado(cliente, sigaa):
    async def cenario():
        await cliente.dados_discente()
        await cliente.agenda()

    rodar(cenario())
    assert sigaa.logins == 1


def test_reloga_quando_a_sessao_expira(cliente, sigaa):
    async def cenario():
        await cliente.dados_discente()
        sigaa.logado = False  # o SIGAA derrubou a sessão
        return await cliente.dados_discente()

    assert rodar(cenario())["Curso"] == "CIÊNCIA DA COMPUTAÇÃO - CONTÍNUA - N"
    assert sigaa.logins == 2


def test_portal_com_tela_intermediaria(cliente, sigaa):
    sigaa.intermediaria = True
    with pytest.raises(SigaaError, match="Portal do discente não carregou"):
        rodar(cliente.turmas())


def test_sessao_expira_no_meio_de_um_clique(cliente, sigaa):
    async def cenario():
        portal = await cliente._portal()
        sigaa.logado = False
        await cliente._acao_menu("Consultar Minhas Notas", portal)

    with pytest.raises(SigaaError, match="Sessão expirou"):
        rodar(cenario())
    assert cliente._logado is False


# --- Charset ------------------------------------------------------------------


def test_html_usa_charset_do_meta_quando_o_header_nao_declara():
    corpo = '<meta charset="utf-8"><p>Cálculo</p>'.encode("utf-8")
    resp = httpx.Response(200, content=corpo, headers={"content-type": "text/html"})
    assert "Cálculo" in SigaaClient._html(resp)


def test_html_cai_para_iso_8859_1_sem_charset():
    resp = httpx.Response(200, content="<p>Cálculo</p>".encode("iso-8859-1"),
                          headers={"content-type": "text/html"})
    assert "Cálculo" in SigaaClient._html(resp)


# --- Menu do portal -----------------------------------------------------------


def test_notas_pelo_menu_do_portal(cliente, sigaa):
    notas = rodar(cliente.notas())
    assert notas[0]["semestre"] == "2026.2"
    assert notas[0]["disciplinas"][0]["unidades"] == {"Unid. 1": 7.0}
    _, path, dados = sigaa.requisicoes[-1]
    assert path == PORTAL_DISCENTE
    assert dados["jscook_action"] == "menu_form_menu_discente_notas"


def test_agenda_do_portal(cliente):
    agenda = rodar(cliente.agenda())
    assert [(a["disciplina"], a["titulo"]) for a in agenda] == [("CÁLCULO I", "Lista 3")]


# --- Turmas virtuais ------------------------------------------------------------


def test_frequencia_de_todas_as_turmas(cliente, sigaa):
    freq = rodar(cliente.frequencia())
    assert [(t["disciplina"], t["dados"]["total_faltas"]) for t in freq] == [
        ("CÁLCULO I", 4),
        ("FÍSICA I", 0),
    ]
    assert sigaa.entradas_em_turma() == ["1", "2"]


def test_filtro_de_disciplina_sem_acento_nem_caixa(cliente, sigaa):
    tarefas = rodar(cliente.tarefas("fisica"))
    assert [t["disciplina"] for t in tarefas] == ["FÍSICA I"]
    assert tarefas[0]["dados"][0]["titulo"] == "Tarefa de FÍSICA I"
    assert sigaa.entradas_em_turma() == ["2"]


def test_filtro_sem_correspondencia_lista_as_turmas(cliente):
    with pytest.raises(SigaaError, match="Turmas disponíveis: CÁLCULO I; FÍSICA I"):
        rodar(cliente.noticias("química"))


def test_conteudo_vem_da_pagina_inicial_da_turma(cliente, sigaa):
    conteudo = rodar(cliente.conteudo("calculo"))
    assert conteudo[0]["dados"][0]["titulo"] == "Aula de CÁLCULO I"
    # Não clicou em nenhum item do menu lateral.
    assert not any("item" in d for _, _, d in sigaa.requisicoes)


def test_cache_evita_entrar_de_novo_na_turma(cliente, sigaa):
    async def cenario():
        await cliente.frequencia()
        await cliente.frequencia("calculo")

    rodar(cenario())
    assert sigaa.entradas_em_turma() == ["1", "2"]


def test_cache_expira(cliente, sigaa, monkeypatch):
    agora = [1000.0]
    # Troca o relógio só no módulo do cliente (o asyncio continua com o relógio real).
    monkeypatch.setattr(client_mod, "time", SimpleNamespace(monotonic=lambda: agora[0]))

    async def cenario():
        await cliente.frequencia("calculo")
        agora[0] += client_mod.CACHE_TTL + 1
        await cliente.frequencia("calculo")

    rodar(cenario())
    assert sigaa.entradas_em_turma() == ["1", "1"]


def test_chamadas_simultaneas_sao_serializadas_pelo_lock(cliente, sigaa):
    async def cenario():
        return await asyncio.gather(cliente.frequencia(), cliente.frequencia())

    a, b = rodar(cenario())
    assert a == b
    # A segunda chamada esperou a primeira e aproveitou o cache.
    assert sigaa.entradas_em_turma() == ["1", "2"]


def test_dados_semana_entra_uma_vez_em_cada_turma(cliente, sigaa):
    dados = rodar(cliente.dados_semana())
    assert set(dados) == {"agenda", "tarefas", "noticias", "frequencia"}
    assert sigaa.entradas_em_turma() == ["1", "2"]
    itens = [d["item"] for _, _, d in sigaa.requisicoes if "item" in d]
    assert itens == ["tarefas", "noticias", "frequencia"] * 2
    assert dados["noticias"][1]["dados"] == [{"titulo": "Aviso de FÍSICA I", "data": "2026-10-27"}]


# --- Calendário acadêmico -------------------------------------------------------


def test_calendario_filtra_presencial_e_convenio_do_curso(cliente, sigaa):
    cal = rodar(cliente.calendario())
    assert cal["semestre"] == "2026.2"
    assert cal["calendario"] == "CALENDÁRIO 2026.2 - CONTÍNUA"
    assert cal["info"] == {"Calendário": "id 2"}
    assert cal["eventos"][0]["inicio"] == "2026-08-01"

    busca = next(d for _, _, d in sigaa.requisicoes if d.get("form:buscar"))
    assert busca["form:modalidade"] == "1"
    assert busca["form:convenio"] == "5"


def test_calendario_de_semestre_inexistente(cliente):
    with pytest.raises(SigaaError, match=r"Calendário de 2030.1 não encontrado.*2026.2"):
        rodar(cliente.calendario("2030.1"))
