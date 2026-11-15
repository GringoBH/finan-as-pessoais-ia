"""Cálculo das datas dos gastos fixos (sem dependências, fácil de testar)."""
import calendar
from datetime import date, timedelta

FREQUENCIAS = ("semanal", "mensal", "anual")


def _com_dia(ano: int, mes: int, dia: int) -> date:
    """Data no mês pedido; se o dia não existir (ex.: 31 em abril), usa o último dia do mês."""
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def proxima_data(atual: date, frequencia: str, dia_base: int) -> date:
    """Próxima cobrança depois de `atual`. `dia_base` é o dia do mês original (para não 'escorregar' de 31 para 28)."""
    if frequencia == "semanal":
        return atual + timedelta(days=7)
    if frequencia == "mensal":
        mes = atual.month % 12 + 1
        ano = atual.year + (1 if atual.month == 12 else 0)
        return _com_dia(ano, mes, dia_base)
    if frequencia == "anual":
        return _com_dia(atual.year + 1, atual.month, dia_base)
    raise ValueError("frequência inválida")
