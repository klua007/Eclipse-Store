"""Utilidades monetárias: sempre centavos inteiros, exibição formatada."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

SYMBOLS = {"BRL": "R$", "USD": "US$", "EUR": "€", "GBP": "£", "JPY": "¥"}

MIN_TOPUP_CENTS = 100          # R$ 1,00
MAX_TOPUP_CENTS = 10_000_000   # R$ 100.000,00 (guarda contra erro de digitação)


class MoneyError(Exception):
    """Valor monetário inválido."""


def parse_cents(text: str) -> int:
    """Converte '10,50' / '10.50' / '10' em centavos inteiros (1050 / 1050 / 1000)."""
    clean = (text or "").strip().replace(" ", "").replace("R$", "")
    if not clean:
        raise MoneyError("Informe um valor.")
    if "," in clean and "." in clean:  # 1.234,56
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:  # 10,50
        clean = clean.replace(",", ".")
    try:
        value = Decimal(clean)
    except InvalidOperation as exc:
        raise MoneyError(f"Valor inválido: {text!r}") from exc
    if not value.is_finite() or value <= 0:
        raise MoneyError("O valor deve ser positivo.")
    cents = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents < MIN_TOPUP_CENTS:
        raise MoneyError(f"Valor mínimo: {format_cents(MIN_TOPUP_CENTS)}.")
    if cents > MAX_TOPUP_CENTS:
        raise MoneyError("Valor acima do limite permitido.")
    return cents


def format_cents(cents: int, currency: str = "BRL") -> str:
    """Formata centavos como moeda legível (ex.: 1050 -> 'R$ 10,50'; 123456 -> 'R$ 1.234,56')."""
    sign = "-" if cents < 0 else ""
    absolute = abs(int(cents))
    symbol = SYMBOLS.get(currency, f"{currency} ")
    if currency == "JPY":  # moeda sem centavos
        whole = f"{absolute:,}".replace(",", ".")
        return f"{sign}{symbol} {whole}"
    whole, rest = divmod(absolute, 100)
    whole_fmt = f"{whole:,}".replace(",", ".")  # separador de milhar pt-BR
    return f"{sign}{symbol} {whole_fmt},{rest:02d}"


def signed_format_cents(cents: int, currency: str = "BRL") -> str:
    """Formata com sinal explícito para extratos (+R$ 10,00 / -R$ 5,00)."""
    if cents > 0:
        return f"+{format_cents(cents, currency)}"
    return format_cents(cents, currency)
