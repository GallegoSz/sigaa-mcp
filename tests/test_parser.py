"""Parsers do HTML do SIGAA (parser.py), com HTML sintético no formato das páginas reais."""

from datetime import date

import pytest

from sigaa_mcp import parser

# --- Utilitários --------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("22/10/2026 23:59", "2026-10-22T23:59"),
        ("até 22/10/2026 às 23h59", "2026-10-22T23:59"),
        ("22/10/2026", "2026-10-22"),
        ("sem data", None),
    ],
)
def test_datahora(texto, esperado):
    assert parser._datahora(texto) == esperado


@pytest.mark.parametrize(("valor", "esperado"), [("7,5", 7.5), (" 10 ", 10.0), ("--", None), ("", None)])
def test_num(valor, esperado):
    assert parser._num(valor) == esperado


def test_normalizar():
    assert parser.normalizar("  Cálculo   Diferencial e\nIntegral ") == "CALCULO DIFERENCIAL E INTEGRAL"


def test_is_login_page():
    assert parser.is_login_page('<input type="password" name="user.senha">')
    assert parser.is_login_page("<input name='user.senha'>")
    assert not parser.is_login_page("<html>portal</html>")


# --- Formulários JSF ----------------------------------------------------------

FORM = """
<form id="menu:form_menu_discente" action="/sigaa/portais/discente/discente.jsf">
  <input type="hidden" name="menu:form_menu_discente" value="menu:form_menu_discente">
  <input type="hidden" name="javax.faces.ViewState" value="j_id42">
  <input type="text" name="busca">
  <input type="submit" name="botao" value="Enviar">
  <select name="ano"><option value="2025">2025</option><option value="2026" selected>2026</option></select>
  <select name="tipo"><option value="A">A</option><option value="B">B</option></select>
  <a id="menu:lnk" onclick="if(typeof jsfcljs == 'function'){jsfcljs(document.getElementById('menu:form_menu_discente'),{'menu:lnk':'menu:lnk','id':'7'},'');}return false">Ver Notas</a>
  <a href="#">Sem onclick</a>
</form>
"""


def test_extract_form():
    action, campos = parser.extract_form(FORM, "menu:form_menu_discente")
    assert action == "/sigaa/portais/discente/discente.jsf"
    assert campos == {
        "menu:form_menu_discente": "menu:form_menu_discente",
        "javax.faces.ViewState": "j_id42",
        "busca": "",
        "ano": "2026",
        "tipo": "A",
    }


def test_extract_form_inexistente():
    with pytest.raises(ValueError, match="Formulário não encontrado"):
        parser.extract_form(FORM, "outro")


def test_extract_select_options():
    assert parser.extract_select_options(FORM, "ano") == [("2025", "2025"), ("2026", "2026")]
    assert parser.extract_select_options(FORM, "nao_existe") == []


def test_extract_link_params_por_texto_e_por_id():
    esperado = {"menu:lnk": "menu:lnk", "id": "7"}
    assert parser.extract_link_params(FORM, "menu:form_menu_discente", rotulo="ver notas") == esperado
    assert parser.extract_link_params(FORM, "menu:form_menu_discente", link_id="menu:lnk") == esperado
    with pytest.raises(ValueError, match="Link não encontrado"):
        parser.extract_link_params(FORM, "menu:form_menu_discente", rotulo="Sem onclick")


def test_extract_menu_action():
    html = """<script>
    var menu = [
      [null, 'Consultar Minhas Notas', 'menu_form_menu_discente_j_id_jsp_1_menu:A]#{ relatorio.notas }', 'menu:form_menu_discente', null],
      [null, 'Calend&aacute;rio Acad&ecirc;mico', 'menu_form_menu_discente_j_id_jsp_2_menu:A]#{ calendario }', 'menu:form_menu_discente', null]
    ];</script>"""
    assert parser.extract_menu_action(html, "consultar minhas notas").startswith(
        "menu_form_menu_discente_j_id_jsp_1"
    )
    assert parser.extract_menu_action(html, "Calendário Acadêmico").startswith(
        "menu_form_menu_discente_j_id_jsp_2"
    )
    with pytest.raises(ValueError, match="Item de menu não encontrado"):
        parser.extract_menu_action(html, "Trancamento")


def test_extract_turmas_virtuais():
    html = """
    <form id="form_acessarTurmaVirtual1"><a onclick="x">CÁLCULO I</a></form>
    <form id="form_acessarTurmaVirtual2"><a onclick="x">  FÍSICA
       I </a></form>
    <form id="outroForm"><a>NÃO É TURMA</a></form>"""
    assert parser.extract_turmas_virtuais(html) == [
        ("form_acessarTurmaVirtual1", "CÁLCULO I"),
        ("form_acessarTurmaVirtual2", "FÍSICA I"),
    ]


# --- Portal do discente -------------------------------------------------------

CABECALHO_NOTAS = (
    "<thead><tr><th>Código</th><th>Disciplina</th><th>Unid. 1</th><th>Unid. 2</th>"
    "<th>Unid. 3</th><th>Recuperação</th><th>Prova Final</th><th>Resultado</th>"
    "<th>Faltas</th><th>Situação</th></tr></thead>"
)


def test_parse_notas():
    html = f"""
    <table class="tabelaRelatorio"><caption>2026.2</caption>{CABECALHO_NOTAS}<tbody>
      <tr><td>MAT01</td><td>CÁLCULO I</td><td>7,5</td><td></td><td></td>
          <td></td><td></td><td></td><td>0</td><td>MATRICULADO</td></tr>
      <tr><td>linha incompleta</td></tr>
    </tbody></table>
    <h3>Semestre 2026.1</h3>
    <table class="tabelaRelatorio">{CABECALHO_NOTAS}<tbody>
      <tr><td>FIS01</td><td>FÍSICA I</td><td>8</td><td>6</td><td>7</td>
          <td>--</td><td>--</td><td>7,0</td><td>4</td><td>APROVADO</td></tr>
    </tbody></table>
    <table class="tabelaRelatorio"><tr><th>Outra coisa</th></tr></table>
    """
    semestres = parser.parse_notas(html)
    assert [s["semestre"] for s in semestres] == ["2026.2", "2026.1"]

    atual = semestres[0]["disciplinas"]
    assert atual == [
        {
            "codigo": "MAT01",
            "disciplina": "CÁLCULO I",
            "unidades": {"Unid. 1": 7.5, "Unid. 2": None, "Unid. 3": None},
            "recuperacao": None,
            "prova_final": None,
            "resultado": None,
            "faltas": None,  # em andamento: o relatório mostra 0 até consolidar
            "situacao": "MATRICULADO",
        }
    ]
    anterior = semestres[1]["disciplinas"][0]
    assert anterior["unidades"] == {"Unid. 1": 8.0, "Unid. 2": 6.0, "Unid. 3": 7.0}
    assert anterior["resultado"] == 7.0
    assert anterior["faltas"] == 4.0


def test_parse_turmas():
    html = """<table>
      <tr><th>Componente Curricular</th><th>Período</th><th>Local</th><th>Horário</th></tr>
      <tr><td>CÁLCULO I</td><td>2026.2</td><td>Sala 10</td><td>2N12</td></tr>
      <tr><td></td><td>x</td><td>x</td><td>x</td></tr>
    </table>"""
    assert parser.parse_turmas(html) == [
        {"componente": "CÁLCULO I", "periodo": "2026.2", "local": "Sala 10", "horario": "2N12"}
    ]
    assert parser.parse_turmas("<table><tr><th>Outra</th></tr></table>") == []


def test_parse_dados_discente():
    html = """<body><table>
      <tr><td>Matrícula:</td><td>202310001</td></tr>
      <tr><td>Curso:</td><td>CIÊNCIA DA COMPUTAÇÃO - CONTÍNUA - N</td></tr>
      <tr><td>CRA:</td><td>8.5</td></tr>
      <tr><td>Cor favorita:</td><td>azul</td></tr>
    </table><div>75,5% Integralizado</div></body>"""
    assert parser.parse_dados_discente(html) == {
        "Matrícula": "202310001",
        "Curso": "CIÊNCIA DA COMPUTAÇÃO - CONTÍNUA - N",
        "CRA": "8.5",
        "Integralizado": "75,5%",
    }


def test_parse_agenda():
    html = """<div id="avaliacao-portal"><table>
      <tr><th>Data</th></tr>
      <tr><td><img></td><td>30/10/2026 23:59</td>
          <td><small>CÁLCULO I<br/>Tarefa: Lista 3</small></td></tr>
      <tr><td><img></td><td>29/10/2026</td>
          <td><small>FÍSICA I<br>Avaliação: Prova 1</small></td></tr>
      <tr><td><img></td><td>sem data</td><td>ignorado</td></tr>
    </table></div>"""
    assert parser.parse_agenda(html, hoje=date(2026, 10, 28)) == [
        {"data": "2026-10-29", "dias_restantes": 1, "disciplina": "FÍSICA I",
         "tipo": "Avaliação", "titulo": "Prova 1"},
        {"data": "2026-10-30T23:59", "dias_restantes": 2, "disciplina": "CÁLCULO I",
         "tipo": "Tarefa", "titulo": "Lista 3"},
    ]
    assert parser.parse_agenda("<html></html>") == []


def test_parse_semestre_atual():
    assert parser.parse_semestre_atual("<body>Semestre atual: 2026.2</body>") == "2026.2"
    assert parser.parse_semestre_atual("<body>nada</body>") is None


# --- Turma virtual --------------------------------------------------------------


def test_parse_frequencia():
    html = """<body>
      <table class="listing"><thead><tr><th>Data</th><th>Situação</th></tr></thead><tbody>
        <tr><td>01/10/2026</td><td>Presente</td></tr>
        <tr><td>08/10/2026</td><td>4 Faltas</td></tr>
        <tr><td>15/10/2026</td><td>2 Faltas</td></tr>
      </tbody></table>
      <p>Frequência: 6</p>
      <p>Número de Aulas Ministradas: 12</p>
      <p>Porcentagem de Frequência em relação as Aulas Ministradas: 50%</p>
      <p>Aulas definidas pela CH do Componente: 72</p>
      <p>Porcentagem de Frequência em relação a CH: 91%</p>
    </body>"""
    freq = parser.parse_frequencia(html)
    assert freq["aulas"] == [
        {"data": "2026-10-01", "situacao": "Presente", "faltas": 0},
        {"data": "2026-10-08", "situacao": "4 Faltas", "faltas": 4},
        {"data": "2026-10-15", "situacao": "2 Faltas", "faltas": 2},
    ]
    assert freq["total_faltas"] == 6
    assert freq["presencas"] == 6
    assert freq["aulas_ministradas"] == 12
    assert freq["percentual_aulas_ministradas"] == 50
    assert freq["aulas_carga_horaria"] == 72
    assert freq["percentual_carga_horaria"] == 91


def test_parse_frequencia_pagina_vazia():
    freq = parser.parse_frequencia("<body>Nenhuma frequência lançada</body>")
    assert freq["aulas"] == [] and freq["total_faltas"] == 0
    assert freq["aulas_carga_horaria"] is None


def test_parse_tarefas():
    html = """<table class="listing"><tbody>
      <tr><td><img></td><td>Lista 1</td>
          <td>De 01/10/2026 às 08h00 até 30/10/2026 às 23h59</td><td>Sim</td><td>1</td>
          <td><a title="Enviar Tarefa">e</a></td></tr>
      <tr><td><p>Resolver exercícios</p><p>1 a 10</p> Baixar arquivo</td></tr>
      <tr><td><img></td><td>Trabalho</td>
          <td>De 01/09/2026 às 08h00 até 15/09/2026 às 23h59</td><td>Não</td><td>0</td>
          <td><a title="Visualizar Tarefa Enviada">v</a></td></tr>
      <tr><td>Ler capítulo 2 Baixar arquivo</td></tr>
    </tbody></table>"""
    lista, trabalho = parser.parse_tarefas(html)
    assert lista == {
        "titulo": "Lista 1",
        "inicio": "2026-10-01T08:00",
        "fim": "2026-10-30T23:59",
        "possui_nota": True,
        "envios": 1,
        "enviada": False,
        "aberta_para_envio": True,
        "descricao": "Resolver exercícios\n1 a 10",
        "tem_anexo": True,
    }
    assert trabalho["possui_nota"] is False
    assert trabalho["enviada"] is True
    assert trabalho["aberta_para_envio"] is False
    assert trabalho["descricao"] == "Ler capítulo 2"
    assert trabalho["tem_anexo"] is True


def test_parse_noticias():
    html = """<table class="listing">
      <tr><th>Título</th><th>Data</th></tr>
      <tr><td>Aviso de prova</td><td>20/10/2026 10:30</td></tr>
      <tr><td>Sem data</td><td>--</td></tr>
    </table>"""
    assert parser.parse_noticias(html) == [{"titulo": "Aviso de prova", "data": "2026-10-20T10:30"}]


def test_parse_avaliacoes_ignora_tabela_com_outro_cabecalho():
    html = """
    <table class="listing"><tr><th>Outra</th></tr><tr><td>01/01/2026</td><td>x</td><td>x</td></tr></table>
    <table class="listing">
      <tr><th>Data</th><th>Hora</th><th>Descrição</th></tr>
      <tr><td>05/11/2026</td><td>19:00</td><td>Prova 1</td></tr>
    </table>"""
    assert parser.parse_avaliacoes(html) == [
        {"data": "2026-11-05", "hora": "19:00", "descricao": "Prova 1"}
    ]


def test_parse_conteudo():
    html = """
    <div class="topico-aula">
      <div class="titulo">Aula 1 - Limites (05/10/2026 - 09/10/2026)</div>
      <div class="conteudotopico">
        <p>Introdução a limites</p>
        <script>fazAlgo()</script>
        <div class="drag_indicator">::</div>
        <div class="item"><img src="/img/icones/pdf.png"><a href="#">Lista 1</a>
          <div class="descricao-item">Exercícios</div></div>
        <div class="item"><img src="/img/icones/video.png">
          <div class="descricao-item">Vídeo sobre limites laterais</div></div>
        <div class="item"><img src="/img/icones/link.png"><a href="#">Site</a></div>
      </div>
    </div>
    <div class="topico-aula"><div class="titulo">Planejamento</div></div>"""
    aula, planejamento = parser.parse_conteudo(html)
    assert aula == {
        "titulo": "Aula 1 - Limites",
        "inicio": "2026-10-05",
        "fim": "2026-10-09",
        "descricao": "Introdução a limites",
        "materiais": [
            {"nome": "Lista 1", "tipo": "arquivo", "descricao": "Exercícios"},
            {"nome": "Vídeo sobre limites laterais", "tipo": "vídeo",
             "descricao": "Vídeo sobre limites laterais"},
            {"nome": "Site", "tipo": "outro", "descricao": ""},
        ],
    }
    assert planejamento == {
        "titulo": "Planejamento", "inicio": None, "fim": None, "descricao": "", "materiais": []
    }


# --- Calendário acadêmico -------------------------------------------------------


def test_parse_lista_calendarios():
    html = """<form id="form"><table>
      <tr><td>Antes de qualquer seção</td>
          <td><a id="form:visualizar0" onclick="jsfcljs(f,{'id':'0'},'')">v</a></td></tr>
      <tr class="secao"><td>Calendários de 2026.2</td></tr>
      <tr><td>CALENDÁRIO 2026.2 - CONTÍNUA</td>
          <td><a id="form:visualizar1" onclick="jsfcljs(document.getElementById('form'),{'form:visualizar1':'form:visualizar1','id':'2'},'')">v</a></td></tr>
      <tr class="secao"><td>Calendários de 2026.1</td></tr>
      <tr><td>CALENDÁRIO 2026.1</td>
          <td><a id="form:visualizar2" onclick="jsfcljs(f,{'id':'1'},'')">v</a></td></tr>
    </table></form>"""
    assert parser.parse_lista_calendarios(html) == [
        {"semestre": "2026.2", "nome": "CALENDÁRIO 2026.2 - CONTÍNUA",
         "params": {"form:visualizar1": "form:visualizar1", "id": "2"}},
        {"semestre": "2026.1", "nome": "CALENDÁRIO 2026.1", "params": {"id": "1"}},
    ]
    assert parser.parse_lista_calendarios("<html></html>") == []


def test_parse_calendario():
    html = """<table class="visualizacao">
      <tr><th>Ano-Período:</th><td>2026.2</td></tr>
      <tr><td>Período Letivo</td></tr>
      <tr><th>Início e fim:</th><td>01/08/2026 a 15/12/2026</td></tr>
      <tr><td></td></tr>
      <tr><th>Consolidação:</th><td>até 20/12/2026</td></tr>
    </table>"""
    assert parser.parse_calendario(html) == {
        "info": {"Ano-Período": "2026.2"},
        "eventos": [
            {"secao": "Período Letivo", "evento": "Início e fim",
             "inicio": "2026-08-01", "fim": "2026-12-15"},
            {"secao": "Período Letivo", "evento": "Consolidação",
             "inicio": "2026-12-20", "fim": "2026-12-20"},
        ],
    }
    assert parser.parse_calendario("<html></html>") == {}
