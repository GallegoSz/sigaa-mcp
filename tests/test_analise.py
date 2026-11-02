"""Regras da UNEMAT aplicadas aos dados já extraídos (analise.py)."""

from datetime import date

import pytest

from sigaa_mcp import analise


def _disciplina(*notas):
    return {
        "disciplina": "CÁLCULO I",
        "unidades": {f"Unid. {i}": n for i, n in enumerate(notas, 1)},
    }


# --- situacao_notas ---------------------------------------------------------


def test_situacao_notas_precisa_de_media_na_unidade_restante():
    r = analise.situacao_notas(_disciplina(5.0, 7.0, None))
    assert r["notas_lancadas"] == [5.0, 7.0]
    assert r["unidades_restantes"] == 1
    assert r["media_parcial"] == 6.0
    # (6 * 3 - 12) / 1 = 6 ; (4 * 3 - 12) / 1 = 0
    assert r["media_necessaria_restantes_para_aprovar"] == 6.0
    assert r["media_necessaria_restantes_para_exame_final"] == 0.0
    assert r["situacao"] == "precisa de média 6.0 nas 1 unidade(s) restante(s)"


def test_situacao_notas_sem_nenhuma_nota_lancada():
    r = analise.situacao_notas(_disciplina(None, None, None))
    assert r["media_parcial"] is None
    assert r["media_necessaria_restantes_para_aprovar"] == 6.0
    assert r["media_necessaria_restantes_para_exame_final"] == 4.0


def test_situacao_notas_ja_garantiu_a_media():
    r = analise.situacao_notas(_disciplina(8.0, 10.0, None))
    assert r["media_necessaria_restantes_para_aprovar"] == 0.0
    assert r["situacao"] == "já garantiu a média 6,0"


def test_situacao_notas_so_alcanca_exame_final():
    r = analise.situacao_notas(_disciplina(1.0, 1.0, None))
    assert r["media_necessaria_restantes_para_aprovar"] == 16.0
    assert r["media_necessaria_restantes_para_exame_final"] == 10.0
    assert r["situacao"] == "não alcança mais a média 6,0; ainda pode ir para o exame final"


def test_situacao_notas_nao_alcanca_nem_exame_final():
    r = analise.situacao_notas(_disciplina(0.0, 0.0, None))
    assert r["situacao"] == "não alcança mais a média para exame final"


def test_situacao_notas_arredonda_erro_de_ponto_flutuante():
    # 18 - 12.3 = 5.699999999999999 em float
    r = analise.situacao_notas(_disciplina(6.5, 5.8, None))
    assert r["media_necessaria_restantes_para_aprovar"] == 5.7


@pytest.mark.parametrize(
    ("notas", "media", "situacao"),
    [
        ((7.0, 8.0, 9.0), 8.0, "aprovado por média"),
        ((6.0, 6.0, 6.0), 6.0, "aprovado por média"),
        ((5.0, 5.0, 5.0), 5.0, "exame final: precisa de 5.0 no exame (se tiver frequência >= 75%)"),
        ((4.0, 4.0, 4.0), 4.0, "exame final: precisa de 5.0 no exame (se tiver frequência >= 75%)"),
        ((2.0, 3.0, 4.0), 3.0, "reprovado por nota"),
    ],
)
def test_situacao_notas_todas_lancadas(notas, media, situacao):
    r = analise.situacao_notas(_disciplina(*notas))
    assert r["unidades_restantes"] == 0
    assert r["media_final"] == media
    assert r["situacao"] == situacao


def test_situacao_notas_disciplina_sem_unidades():
    r = analise.situacao_notas({"disciplina": "TCC", "unidades": {}})
    assert r["media_parcial"] is None
    assert r["situacao"] == "sem notas por unidade no SIGAA"


# --- alerta_frequencia ------------------------------------------------------


def _freq(faltas_por_aula, carga=72, **extra):
    aulas = [{"data": None, "situacao": "", "faltas": f} for f in faltas_por_aula]
    return {
        "aulas": aulas,
        "total_faltas": sum(faltas_por_aula),
        "aulas_carga_horaria": carga,
        "percentual_aulas_ministradas": 83,
        **extra,
    }


def test_alerta_frequencia_ok():
    r = analise.alerta_frequencia("CÁLCULO I", _freq([0, 4, 0, 4]))
    assert r == {
        "disciplina": "CÁLCULO I",
        "faltas": 8,
        "aulas_carga_horaria": 72,
        "percentual_presenca_aulas_ministradas": 83,
        "limite_faltas": 18,
        "faltas_restantes": 10,
        "aulas_por_encontro": 4,
        "encontros_que_ainda_pode_faltar": 2,
        "status": "ok",
    }


@pytest.mark.parametrize(
    ("faltas", "restantes", "encontros", "status"),
    [
        ([4, 4, 4], 6, 1, "atenção: só pode faltar mais 1 encontro"),
        ([4, 4, 4, 4], 2, 0, "crítico: não pode faltar mais nenhum encontro"),
        ([4, 4, 4, 4, 4], -2, 0, "REPROVADO POR FALTA (pelos registros atuais)"),
    ],
)
def test_alerta_frequencia_status(faltas, restantes, encontros, status):
    r = analise.alerta_frequencia("X", _freq(faltas))
    assert r["faltas_restantes"] == restantes
    assert r["encontros_que_ainda_pode_faltar"] == encontros
    assert r["status"] == status


def test_alerta_frequencia_limite_arredonda_para_baixo():
    # 30 * 25% = 7,5 -> 7
    assert analise.alerta_frequencia("X", _freq([], carga=30))["limite_faltas"] == 7


def test_alerta_frequencia_estima_aulas_por_encontro_sem_faltas():
    r = analise.alerta_frequencia("X", _freq([0] * 10, carga=60, aulas_ministradas=20))
    assert r["aulas_por_encontro"] == 2
    assert r["faltas_restantes"] == 15
    assert r["encontros_que_ainda_pode_faltar"] == 7
    assert r["status"] == "ok"


def test_alerta_frequencia_sem_carga_horaria():
    r = analise.alerta_frequencia("X", _freq([2], carga=None))
    assert r["status"] == "sem dados de carga horária"
    assert "limite_faltas" not in r


# --- resumo_semana ----------------------------------------------------------

HOJE = date(2026, 10, 28)


def _tarefa(titulo, fim, aberta=True, enviada=False):
    return {"titulo": titulo, "fim": fim, "aberta_para_envio": aberta, "enviada": enviada}


def test_resumo_semana():
    agenda = [{"titulo": f"d{d}", "dias_restantes": d} for d in (-1, 0, 7, 8)]
    tarefas = [
        {
            "disciplina": "CÁLCULO I",
            "dados": [
                _tarefa("aberta", "2026-10-30T23:59"),
                _tarefa("enviada", "2026-10-30T23:59", enviada=True),
                _tarefa("fechada", "2026-10-30T23:59", aberta=False),
                _tarefa("vencida", "2026-10-27T23:59"),
                _tarefa("sem prazo", None),
                _tarefa("vence hoje", "2026-10-28T08:00"),
            ],
        }
    ]
    noticias = [
        {
            "disciplina": "FÍSICA I",
            "dados": [
                {"titulo": "limite da janela", "data": "2026-10-21"},
                {"titulo": "antiga", "data": "2026-10-20"},
                {"titulo": "hoje", "data": "2026-10-28T10:00"},
                {"titulo": "futura", "data": "2026-10-29"},
                {"titulo": "sem data", "data": None},
            ],
        }
    ]
    alertas = [{"disciplina": "A", "status": "ok"}, {"disciplina": "B", "status": "crítico"}]

    r = analise.resumo_semana(agenda, tarefas, noticias, alertas, hoje=HOJE, dias=7)

    assert r["periodo"] == "2026-10-28 a 2026-11-04"
    assert [a["titulo"] for a in r["agenda"]] == ["d0", "d7"]
    assert r["tarefas_pendentes"] == [
        {"disciplina": "CÁLCULO I", "titulo": "vence hoje", "fim": "2026-10-28T08:00", "dias_restantes": 0},
        {"disciplina": "CÁLCULO I", "titulo": "aberta", "fim": "2026-10-30T23:59", "dias_restantes": 2},
    ]
    assert [n["titulo"] for n in r["noticias_recentes"]] == ["limite da janela", "hoje"]
    assert r["noticias_recentes"][0]["disciplina"] == "FÍSICA I"
    assert r["alertas_frequencia"] == [{"disciplina": "B", "status": "crítico"}]
