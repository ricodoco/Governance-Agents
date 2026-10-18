"""Integer-cent money math. No floats anywhere in the gate."""


def _check_int(name: str, v: int, minimum: int = 0) -> None:
    if not isinstance(v, int) or isinstance(v, bool) or v < minimum:
        raise ValueError(f"{name} must be an int >= {minimum}, got {v!r}")


def landed_cents(unit_cents: int, qty: int, tax_bp: int,
                 shipping_cents: int, fees_cents: int) -> int:
    """Total landed cost: price*qty + tax + shipping + fees, in cents.

    tax_bp is tax in basis points of the subtotal (825 = 8.25%), rounded half up.
    """
    _check_int("unit_cents", unit_cents)
    _check_int("qty", qty, 1)
    _check_int("tax_bp", tax_bp)
    _check_int("shipping_cents", shipping_cents)
    _check_int("fees_cents", fees_cents)
    sub = unit_cents * qty
    tax = (sub * tax_bp + 5000) // 10000
    return sub + tax + shipping_cents + fees_cents
