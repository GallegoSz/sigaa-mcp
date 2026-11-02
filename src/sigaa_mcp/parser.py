"""Parsers do HTML do SIGAA (UNEMAT)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from html import unescape

from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

_SEMESTRE_RE = re.compile(r"\b(\d{4}\.\d)\b")
_DATA_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s+(?:às\s+)?(\d{2})[:h](\d{2}))?")


def _text(node: Node | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(separator=" ")).strip()


def _num(valor: str) -> float | None:
    valor = valor.strip().replace(",", ".")
    try:
        return float(valor)
    except ValueError:
        return None


def _datahora(texto: str) -> str | None:
    """'22/10/2026 23:59' ou '22/10/2026 às 23h59' -> ISO 8601."""
    m = _DATA_RE.search(texto)
    if not m:
        return None
    dia, mes, ano, hora, minuto = m.groups()
    if hora:
        return f"{ano}-{mes}-{dia}T{hora}:{minuto}"
    return f"{ano}-{mes}-{dia}"


def normalizar(texto: str) -> str:
    """Remove acentos e caixa para comparar nomes de disciplinas."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().upper()


def is_login_page(html: str) -> bool:
    return 'name="user.senha"' in html or "name='user.senha'" in html


# --- Formulários JSF ------------------------------------------------------


def extract_form(html: str, form_id: str) -> tuple[str, dict[str, str]]:
    """Action e campos de um formulário JSF (inputs e selects, sem os botões)."""
    form = HTMLParser(html).css_first(f'form[id="{form_id}"]')
    if form is None:
        raise ValueError(f"Formulário não encontrado: {form_id}")
    campos = {}
    for inp in form.css("input"):
        nome = inp.attributes.get("name")
        tipo = (inp.attributes.get("type") or "text").lower()
        if nome and tipo not in {"submit", "button", "image", "reset"}:
            campos[nome] = inp.attributes.get("value") or ""
    for sel in form.css("select"):
        nome = sel.attributes.get("name")
        if not nome:
            continue
        opcao = sel.css_first("option[selected]") or sel.css_first("option")
        campos[nome] = (opcao.attributes.get("value") or "") if opcao else ""
    return form.attributes.get("action") or "", campos


def extract_select_options(html: str, nome: str) -> list[tuple[str, str]]:
    """(valor, texto) das opções de um <select> pelo name."""
    sel = HTMLParser(html).css_first(f'select[name="{nome}"]')
    if sel is None:
        return []
    return [(o.attributes.get("value") or "", _text(o)) for o in sel.css("option")]


def _params_onclick(onclick: str) -> dict[str, str]:
    """Pares {'chave':'valor'} passados ao jsfcljs(form, {...}, '') de um commandLink."""
    m = re.search(r"jsfcljs\([^,]+,\s*\{([^}]*)\}", onclick)
    return dict(re.findall(r"'([^']+)'\s*:\s*'([^']*)'", m.group(1))) if m else {}


def extract_link_params(
    html: str, form_id: str, rotulo: str | None = None, link_id: str | None = None
) -> dict[str, str]:
    """Parâmetros que um commandLink JSF envia ao ser clicado, buscado pelo texto ou id."""
    form = HTMLParser(html).css_first(f'form[id="{form_id}"]')
    if form is None:
        raise ValueError(f"Formulário não encontrado: {form_id}")
    for a in form.css("a"):
        if link_id is not None and a.attributes.get("id") != link_id:
            continue
        if rotulo is not None and _text(a).lower() != rotulo.lower():
            continue
        params = _params_onclick(a.attributes.get("onclick") or "")
        if params:
            return params
    raise ValueError(f"Link não encontrado: {rotulo or link_id!r}")


def extract_menu_action(html: str, rotulo: str) -> str:
    """Valor de jscook_action para um item do menu (ex.: 'Consultar Minhas Notas').

    O menu JSCookMenu é montado em JS com itens no formato
    [icone, 'Rótulo', 'acao', 'form', null]. O id gerado pelo JSF muda
    entre versões, então ele é lido em tempo de execução.
    """
    for m in re.finditer(r"'([^']*)',\s*'(menu_form_menu_discente[^']*)'", html):
        if unescape(m.group(1)).strip().lower() == rotulo.lower():
            return m.group(2)
    raise ValueError(f"Item de menu não encontrado: {rotulo!r}")


def extract_turmas_virtuais(html: str) -> list[tuple[str, str]]:
    """(id do formulário, nome da disciplina) de cada turma do portal."""
    turmas = []
    for form in HTMLParser(html).css('form[id^="form_acessarTurmaVirtual"]'):
        a = form.css_first("a")
        if a is not None:
            turmas.append((form.attributes["id"], _text(a)))
    return turmas


# --- Portal do discente -----------------------------------------------------


def _semestre_da_tabela(table: Node) -> str | None:
    caption = table.css_first("caption")
    if caption is not None:
        m = _SEMESTRE_RE.search(_text(caption))
        if m:
            return m.group(1)
    # Fallback: procura o semestre nos elementos anteriores à tabela.
    anterior = table.prev
    for _ in range(10):
        if anterior is None:
            break
        m = _SEMESTRE_RE.search(_text(anterior))
        if m:
            return m.group(1)
        anterior = anterior.prev
    return None


def parse_notas(html: str) -> list[dict]:
    """Lê o relatório 'Consultar Minhas Notas' agrupado por semestre."""
    tree = HTMLParser(html)
    semestres = []
    for table in tree.css("table.tabelaRelatorio"):
        cabecalhos = [_text(th) for th in table.css("thead th") or table.css("th")]
        if not any("Disciplina" in c for c in cabecalhos):
            continue
        disciplinas = []
        for tr in table.css("tbody tr"):
            celulas = [_text(td) for td in tr.css("td")]
            if len(celulas) < len(cabecalhos):
                continue
            linha = dict(zip(cabecalhos, celulas))
            unidades = {
                k: _num(v) for k, v in linha.items() if k.lower().startswith("unid")
            }
            situacao = linha.get("Situação", "")
            # O relatório só registra as faltas quando a turma é consolidada;
            # antes disso vem 0 mesmo havendo faltas (use a frequência da turma).
            faltas = _num(linha.get("Faltas", "")) if situacao != "MATRICULADO" else None
            disciplinas.append(
                {
                    "codigo": linha.get("Código", ""),
                    "disciplina": linha.get("Disciplina", ""),
                    "unidades": unidades,
                    "recuperacao": _num(linha.get("Recuperação", "")),
                    "prova_final": _num(linha.get("Prova Final", "")),
                    "resultado": _num(linha.get("Resultado", "")),
                    "faltas": faltas,
                    "situacao": situacao,
                }
            )
        semestres.append(
            {"semestre": _semestre_da_tabela(table), "disciplinas": disciplinas}
        )
    return semestres


def parse_turmas(html: str) -> list[dict]:
    """Turmas do semestre listadas no portal do discente."""
    tree = HTMLParser(html)
    for table in tree.css("table"):
        cabecalhos = [_text(th) for th in table.css("th")]
        if "Componente Curricular" not in cabecalhos or "Horário" not in cabecalhos:
            continue
        turmas = []
        for tr in table.css("tr"):
            celulas = [_text(td) for td in tr.css("td")]
            if len(celulas) < 4 or not celulas[0]:
                continue
            turmas.append(
                {
                    "componente": celulas[0],
                    "periodo": celulas[1],
                    "local": celulas[2],
                    "horario": celulas[3],
                }
            )
        return turmas
    return []


_ROTULOS_DISCENTE = {
    "Matrícula",
    "Curso",
    "Nível",
    "Status",
    "E-Mail Institucional",
    "Entrada",
    "CRA",
    "CH. Obrigatória Pendente",
    "CH. Eletiva Pendente",
    "CH. Total Currículo",
}


def parse_dados_discente(html: str) -> dict[str, str]:
    """Dados institucionais e índices acadêmicos do portal (Matrícula, Curso, CRA...)."""
    tree = HTMLParser(html)
    dados: dict[str, str] = {}
    for tr in tree.css("tr"):
        tds = tr.css("td")
        if len(tds) != 2:
            continue
        rotulo = _text(tds[0]).rstrip(":").strip()
        if rotulo in _ROTULOS_DISCENTE:
            dados[rotulo] = _text(tds[1])
    m = re.search(r"(\d+(?:[.,]\d+)?)%\s*Integralizado", tree.body.text() if tree.body else "")
    if m:
        dados["Integralizado"] = f"{m.group(1)}%"
    return dados


def parse_agenda(html: str, hoje: date | None = None) -> list[dict]:
    """Bloco 'Minhas atividades' do portal: tarefas e avaliações com data."""
    hoje = hoje or date.today()
    box = HTMLParser(html).css_first("#avaliacao-portal")
    if box is None:
        return []
    atividades = []
    for tr in box.css("tr"):
        tds = tr.css("td")
        if len(tds) < 3:
            continue
        quando = _datahora(_text(tds[1]))
        if quando is None:
            continue
        descricao = tds[2].css_first("small") or tds[2]
        partes = [
            _text(HTMLParser(p).body) for p in re.split(r"<br\s*/?>", descricao.html, flags=re.I)
        ]
        disciplina = partes[0] if partes else ""
        detalhe = " ".join(partes[1:])
        tipo, _, titulo = detalhe.partition(":")
        dia = datetime.fromisoformat(quando).date()
        atividades.append(
            {
                "data": quando,
                "dias_restantes": (dia - hoje).days,
                "disciplina": disciplina,
                "tipo": tipo.strip(),
                "titulo": titulo.strip(),
            }
        )
    return sorted(atividades, key=lambda a: a["data"])


# --- Turma virtual ----------------------------------------------------------


def parse_frequencia(html: str) -> dict:
    """Página 'Frequência' da turma virtual."""
    tree = HTMLParser(html)
    aulas = []
    for table in tree.css("table.listing"):
        if [_text(th) for th in table.css("th")][:2] != ["Data", "Situação"]:
            continue
        for tr in table.css("tbody tr"):
            tds = tr.css("td")
            if len(tds) < 2:
                continue
            situacao = _text(tds[1])
            m = re.search(r"(\d+)\s*Falta", situacao)
            aulas.append(
                {
                    "data": _datahora(_text(tds[0])),
                    "situacao": situacao,
                    "faltas": int(m.group(1)) if m else 0,
                }
            )
        break
    texto = re.sub(r"\s+", " ", tree.body.text() if tree.body else "")

    def _int(padrao: str) -> int | None:
        m = re.search(padrao, texto)
        return int(m.group(1)) if m else None

    return {
        "aulas": aulas,
        "total_faltas": sum(a["faltas"] for a in aulas),
        "presencas": _int(r"Frequência:\s*(\d+)"),
        "aulas_ministradas": _int(r"Número de Aulas Ministradas:\s*(\d+)"),
        "percentual_aulas_ministradas": _int(r"relação as Aulas Ministradas:\s*(\d+)%"),
        "aulas_carga_horaria": _int(r"Aulas definidas pela CH do Componente:\s*(\d+)"),
        "percentual_carga_horaria": _int(r"em relação a CH:\s*(\d+)%"),
    }


def parse_tarefas(html: str) -> list[dict]:
    """Página 'Tarefas' da turma virtual.

    Cada tarefa ocupa duas linhas: uma com os dados e outra (uma única
    célula) com o enunciado.
    """
    tree = HTMLParser(html)
    tarefas: list[dict] = []
    for table in tree.css("table.listing"):
        for tr in table.css("tbody tr"):
            tds = tr.css("td")
            if len(tds) >= 5:
                periodo = _text(tds[2])
                datas = re.findall(r"\d{2}/\d{2}/\d{4} às \d{2}h\d{2}", periodo)
                titulos_links = [a.attributes.get("title") or "" for a in tr.css("a")]
                tarefas.append(
                    {
                        "titulo": _text(tds[1]),
                        "inicio": _datahora(datas[0]) if datas else None,
                        "fim": _datahora(datas[1]) if len(datas) > 1 else None,
                        "possui_nota": _text(tds[3]).lower() == "sim",
                        "envios": int(_num(_text(tds[4])) or 0),
                        "enviada": any("Enviada" in t for t in titulos_links),
                        "aberta_para_envio": any(
                            t.lower() == "enviar tarefa" for t in titulos_links
                        ),
                        "descricao": "",
                        "tem_anexo": False,
                    }
                )
            elif len(tds) == 1 and tarefas:
                partes = [_text(p) for p in tds[0].css("p")] or [_text(tds[0])]
                descricao = "\n".join(p for p in partes if p)
                tarefas[-1]["descricao"] = re.sub(r"\s*Baixar arquivo$", "", descricao)
                tarefas[-1]["tem_anexo"] = "Baixar arquivo" in _text(tds[0])
        if tarefas:
            break
    return tarefas


def parse_noticias(html: str) -> list[dict]:
    """Página 'Notícias' da turma virtual (título e data)."""
    noticias = []
    for table in HTMLParser(html).css("table.listing"):
        for tr in table.css("tr"):
            tds = tr.css("td")
            if len(tds) >= 2 and _datahora(_text(tds[1])):
                noticias.append({"titulo": _text(tds[0]), "data": _datahora(_text(tds[1]))})
        if noticias:
            break
    return noticias


def parse_avaliacoes(html: str) -> list[dict]:
    """Página 'Avaliações' da turma virtual (datas de prova marcadas pelo professor)."""
    avaliacoes = []
    for table in HTMLParser(html).css("table.listing"):
        if [_text(th) for th in table.css("th")][:3] != ["Data", "Hora", "Descrição"]:
            continue
        for tr in table.css("tr"):
            tds = tr.css("td")
            if len(tds) >= 3 and _datahora(_text(tds[0])):
                avaliacoes.append(
                    {
                        "data": _datahora(_text(tds[0])),
                        "hora": _text(tds[1]),
                        "descricao": _text(tds[2]),
                    }
                )
        break
    return avaliacoes


_TIPOS_ITEM = {"pdf": "arquivo", "video": "vídeo", "tarefa": "tarefa"}


def parse_conteudo(html: str) -> list[dict]:
    """Tópicos de aula da página principal da turma virtual (cronograma + materiais)."""
    topicos = []
    for topico in HTMLParser(html).css("div.topico-aula"):
        titulo = _text(topico.css_first("div.titulo"))
        datas = re.findall(r"\d{2}/\d{2}/\d{4}", titulo)
        corpo = topico.css_first("div.conteudotopico")
        itens = []
        if corpo is not None:
            for node in corpo.css("script, div.drag_indicator"):
                node.decompose()
            for item in corpo.css("div.item"):
                icones = [
                    (img.attributes.get("src") or "").rsplit("/", 1)[-1].split(".")[0]
                    for img in item.css("img")
                ]
                tipo = next((_TIPOS_ITEM[i] for i in icones if i in _TIPOS_ITEM), "outro")
                descricao = _text(item.css_first("div.descricao-item"))
                # Vídeos não têm link com nome; usa o início da descrição no lugar.
                nome = _text(item.css_first("a")) or descricao[:80]
                itens.append({"nome": nome, "tipo": tipo, "descricao": descricao})
                item.decompose()
        topicos.append(
            {
                "titulo": re.sub(r"\s*\(\d{2}/\d{2}/\d{4}.*$", "", titulo).strip(),
                "inicio": _datahora(datas[0]) if datas else None,
                "fim": _datahora(datas[-1]) if datas else None,
                "descricao": _text(corpo),
                "materiais": itens,
            }
        )
    return topicos


def parse_semestre_atual(html: str) -> str | None:
    """Semestre exibido no cabeçalho do portal ('Semestre atual: 2026.2')."""
    m = re.search(r"Semestre atual:\s*(\d{4}\.\d)", HTMLParser(html).body.text() or "")
    return m.group(1) if m else None


def parse_lista_calendarios(html: str) -> list[dict]:
    """Resultado da busca de calendários: semestre, nome e parâmetros do link 'Visualizar'."""
    form = HTMLParser(html).css_first('form[id="form"]')
    calendarios = []
    semestre = None
    for tr in form.css("tr") if form else []:
        if "secao" in (tr.attributes.get("class") or ""):
            m = _SEMESTRE_RE.search(_text(tr))
            semestre = m.group(1) if m else None
            continue
        link = tr.css_first('a[id^="form:visualizar"]')
        if link is None or semestre is None:
            continue
        calendarios.append(
            {
                "semestre": semestre,
                "nome": _text(tr.css_first("td")),
                "params": _params_onclick(link.attributes.get("onclick") or ""),
            }
        )
    return calendarios


def parse_calendario(html: str) -> dict:
    """Tabela de visualização do calendário acadêmico (rótulo -> período)."""
    table = HTMLParser(html).css_first("table.visualizacao")
    if table is None:
        return {}
    info: dict[str, str] = {}
    eventos = []
    secao = "Geral"
    for tr in table.css("tr"):
        th, td = tr.css_first("th"), tr.css_first("td")
        if th is None:
            if td is not None and _text(td):
                secao = _text(td)
            continue
        rotulo, valor = _text(th).rstrip(":").strip(), _text(td)
        datas = re.findall(r"\d{2}/\d{2}/\d{4}", valor)
        if datas:
            eventos.append(
                {
                    "secao": secao,
                    "evento": rotulo,
                    "inicio": _datahora(datas[0]),
                    "fim": _datahora(datas[-1]),
                }
            )
        else:
            info[rotulo] = valor
    return {"info": info, "eventos": eventos}
