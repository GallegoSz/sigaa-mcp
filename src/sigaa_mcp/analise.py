"""Cálculos sobre os dados do SIGAA, segundo a Normatização Acadêmica da UNEMAT.

Resolução nº 001/2024-CONEPE:
- Art. 61: média semestral >= 6,0 aprova por média; com frequência >= 75% e média
  entre 4,0 e 5,9 o aluno tem direito a exame final, e aprova com nota >= 5,0 no exame.
- Art. 43: frequência abaixo de 75% da carga horária presencial reprova,
  independentemente da nota.

A média semestral é tratada como média aritmética das unidades, que é o padrão
do SIGAA; o plano de curso de cada disciplina pode definir pesos diferentes.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

MEDIA_APROVACAO = 6.0
MEDIA_EXAME_FINAL = 4.0
NOTA_EXAME_FINAL = 5.0
FREQUENCIA_MINIMA = 0.75


def _arred(valor: float) -> float:
    return round(valor + 1e-9, 2)


def situacao_notas(disciplina: dict) -> dict:
    """Quanto falta tirar nas unidades restantes de uma disciplina de get_notas."""
    unidades = disciplina["unidades"]
    lancadas = [n for n in unidades.values() if n is not None]
    total = len(unidades)
    restantes = total - len(lancadas)
    soma = sum(lancadas)
    resultado = {
        "disciplina": disciplina["disciplina"],
        "notas_lancadas": lancadas,
        "unidades_restantes": restantes,
        "media_parcial": _arred(soma / len(lancadas)) if lancadas else None,
    }
    if total == 0:
        # Ex.: estágio/TCC, que não têm colunas de unidade no relatório.
        resultado["situacao"] = "sem notas por unidade no SIGAA"
        return resultado
    if restantes == 0:
        media = soma / total
        resultado["media_final"] = _arred(media)
        if media >= MEDIA_APROVACAO:
            resultado["situacao"] = "aprovado por média"
        elif media >= MEDIA_EXAME_FINAL:
            resultado["situacao"] = (
                f"exame final: precisa de {NOTA_EXAME_FINAL} no exame "
                "(se tiver frequência >= 75%)"
            )
        else:
            resultado["situacao"] = "reprovado por nota"
        return resultado

    def _precisa(meta: float) -> float:
        return _arred(max(0.0, (meta * total - soma) / restantes))

    para_aprovar = _precisa(MEDIA_APROVACAO)
    para_exame = _precisa(MEDIA_EXAME_FINAL)
    resultado["media_necessaria_restantes_para_aprovar"] = para_aprovar
    resultado["media_necessaria_restantes_para_exame_final"] = para_exame
    if para_aprovar > 10:
        resultado["situacao"] = (
            "não alcança mais a média 6,0; ainda pode ir para o exame final"
            if para_exame <= 10
            else "não alcança mais a média para exame final"
        )
    elif para_aprovar == 0:
        resultado["situacao"] = "já garantiu a média 6,0"
    else:
        resultado["situacao"] = (
            f"precisa de média {para_aprovar} nas {restantes} unidade(s) restante(s)"
        )
    return resultado


def alerta_frequencia(disciplina: str, freq: dict) -> dict:
    """Quantas faltas ainda cabem antes de reprovar por frequência (Art. 43)."""
    carga = freq.get("aulas_carga_horaria")
    faltas = freq.get("total_faltas", 0)
    encontros = [a for a in freq.get("aulas", []) if a["faltas"]]
    # Aulas (horas-aula) por encontro: a falta num dia vale todas as aulas daquele dia.
    aulas_por_encontro = max((a["faltas"] for a in encontros), default=None)
    if aulas_por_encontro is None and freq.get("aulas") and freq.get("aulas_ministradas"):
        aulas_por_encontro = round(freq["aulas_ministradas"] / len(freq["aulas"])) or 1
    resultado = {
        "disciplina": disciplina,
        "faltas": faltas,
        "aulas_carga_horaria": carga,
        "percentual_presenca_aulas_ministradas": freq.get("percentual_aulas_ministradas"),
    }
    if not carga:
        resultado["status"] = "sem dados de carga horária"
        return resultado
    limite = math.floor(carga * (1 - FREQUENCIA_MINIMA))
    restantes = limite - faltas
    resultado["limite_faltas"] = limite
    resultado["faltas_restantes"] = restantes
    if aulas_por_encontro:
        resultado["aulas_por_encontro"] = aulas_por_encontro
        resultado["encontros_que_ainda_pode_faltar"] = max(0, restantes // aulas_por_encontro)
    if restantes < 0:
        resultado["status"] = "REPROVADO POR FALTA (pelos registros atuais)"
    elif aulas_por_encontro and restantes < aulas_por_encontro:
        resultado["status"] = "crítico: não pode faltar mais nenhum encontro"
    elif aulas_por_encontro and restantes < 2 * aulas_por_encontro:
        resultado["status"] = "atenção: só pode faltar mais 1 encontro"
    else:
        resultado["status"] = "ok"
    return resultado


def _data(valor: str | None) -> date | None:
    return datetime.fromisoformat(valor).date() if valor else None


def resumo_semana(
    agenda: list[dict],
    tarefas: list[dict],
    noticias: list[dict],
    alertas: list[dict],
    hoje: date | None = None,
    dias: int = 7,
) -> dict:
    """Junta o que importa para os próximos dias num único resultado."""
    hoje = hoje or date.today()
    ate = hoje + timedelta(days=dias)
    proximos = [a for a in agenda if 0 <= a["dias_restantes"] <= dias]
    pendentes = []
    for turma in tarefas:
        for t in turma["dados"]:
            fim = _data(t["fim"])
            if t["aberta_para_envio"] and not t["enviada"] and fim and fim >= hoje:
                pendentes.append(
                    {
                        "disciplina": turma["disciplina"],
                        "titulo": t["titulo"],
                        "fim": t["fim"],
                        "dias_restantes": (fim - hoje).days,
                    }
                )
    recentes = [
        {"disciplina": turma["disciplina"], **n}
        for turma in noticias
        for n in turma["dados"]
        if (d := _data(n["data"])) and hoje - timedelta(days=dias) <= d <= hoje
    ]
    return {
        "periodo": f"{hoje.isoformat()} a {ate.isoformat()}",
        "agenda": proximos,
        "tarefas_pendentes": sorted(pendentes, key=lambda t: t["fim"]),
        "noticias_recentes": recentes,
        "alertas_frequencia": [a for a in alertas if a.get("status") != "ok"],
    }
