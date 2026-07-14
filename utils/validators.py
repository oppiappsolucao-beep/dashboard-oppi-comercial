import re


def validate_email(value: str) -> bool:
    if not value:
        return False
    pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    return bool(re.match(pattern, value.strip()))


def validate_phone(value: str) -> bool:
    if not value:
        return False
    digits = re.sub(r"\D", "", value)
    return len(digits) in (10, 11)


def validate_cnpj(value: str) -> bool:
    if not value:
        return False
    digits = re.sub(r"\D", "", value)
    if len(digits) != 14 or digits == digits[0] * 14:
        return False

    def calc(digs, weights):
        total = sum(int(d) * w for d, w in zip(digs, weights))
        rest = total % 11
        return "0" if rest < 2 else str(11 - rest)

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6] + w1
    d1 = calc(digits[:12], w1)
    d2 = calc(digits[:12] + d1, w2)
    return digits[-2:] == d1 + d2


def validate_required(value: str, label: str) -> str | None:
    if not str(value or "").strip():
        return f"O campo {label} é obrigatório."
    return None
