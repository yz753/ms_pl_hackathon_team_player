"""Number formatting helpers for JSON outputs and display text."""


def r(x, n=3):
    """Round to n decimals; None stays None."""
    return None if x is None else round(float(x), n)


def z(x, n=3):
    """Round without producing negative zero (-0.000 in text)."""
    return round(float(x or 0), n) + 0.0


def pp(x):
    """Probability difference expressed as percentage points."""
    return None if x is None else round(100 * x, 2)
