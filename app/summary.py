"""GTIN/batch weight aggregation for the ``/weight-summary`` endpoint.

Each accepted scan must contain exactly one AI 01 (GTIN), optionally one
AI 10 (batch/lot) and exactly one weight element (310n kg or 320n lb).
Scans are grouped by ``(GTIN, batch)`` in first-appearance order and their
weights are summed with :class:`decimal.Decimal` only: the weight is
rebuilt from the parser's original six-digit value and the precision
digit (never from the float ``decimal_value``), pounds are converted with
the exact factor ``1 lb = 0.45359237 kg``, and the per-group total is
converted to the target unit before six-decimal half-even rounding.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from .gs1 import GS1ParseError, WEIGHT_AI_FAMILIES, parse

# Exact international avoirdupois pound definition.
LB_TO_KG = Decimal("0.45359237")
SIX_PLACES = Decimal("0.000001")


class ScanFailure(Exception):
    """One scan failed to parse or violates the summary invariants.

    Carries the zero-based index of the scan inside the request and the
    original :class:`GS1ParseError` so the endpoint can report both.
    """

    def __init__(self, index: int, cause: GS1ParseError):
        super().__init__(f"scans[{index}]: {cause.message}")
        self.index = index
        self.cause = cause


def _fail(index: int, code: str, message: str, expected: str, offset: int, found=None):
    raise ScanFailure(
        index,
        GS1ParseError(code, message, offset=offset, expected=expected, found=found),
    )


def _extract(index: int, scan: str) -> tuple[str, str | None, Decimal]:
    """Parse one scan and return ``(gtin, batch, weight_in_kg)``.

    Raises :class:`ScanFailure` for parser errors, missing/duplicated key
    AIs, or a missing/duplicated weight element.
    """
    try:
        elements = parse(scan)
    except GS1ParseError as exc:
        raise ScanFailure(index, exc) from exc

    gtins = [e for e in elements if e.ai == "01"]
    batches = [e for e in elements if e.ai == "10"]
    weights = [e for e in elements if e.ai[:3] in WEIGHT_AI_FAMILIES]

    if not gtins:
        _fail(
            index,
            "MISSING_GTIN",
            "scan is missing required AI 01 (GTIN)",
            offset=len(scan.encode("utf-8")),
            expected="exactly one AI 01 (GTIN)",
            found="no AI 01",
        )
    if len(gtins) > 1:
        _fail(
            index,
            "DUPLICATE_AI",
            f"AI 01 (GTIN) appears {len(gtins)} times; it may appear exactly once",
            offset=gtins[1].offset,
            expected="exactly one AI 01 (GTIN)",
            found="01",
        )

    if len(batches) > 1:
        _fail(
            index,
            "DUPLICATE_AI",
            f"AI 10 (BATCH/LOT) appears {len(batches)} times; it may appear at most once",
            offset=batches[1].offset,
            expected="at most one AI 10 (BATCH/LOT)",
            found="10",
        )

    if not weights:
        _fail(
            index,
            "MISSING_WEIGHT",
            "scan is missing the required weight AI (one 310n or 320n)",
            offset=len(scan.encode("utf-8")),
            expected="exactly one weight AI: 310n (kg) or 320n (lb)",
            found="no 310n/320n",
        )
    if len(weights) > 1:
        _fail(
            index,
            "DUPLICATE_WEIGHT",
            f"scan contains {len(weights)} weight elements; exactly one 310n/320n is allowed",
            offset=weights[1].offset,
            expected="exactly one weight AI: 310n (kg) or 320n (lb)",
            found=weights[1].ai,
        )

    gtin = gtins[0].value
    batch = batches[0].value if batches else None
    return gtin, batch, _weight_in_kg(weights[0])


def _weight_in_kg(element) -> Decimal:
    """Rebuild the weight from the raw six digits and precision digit.

    Equivalent to ``int(value) / 10**n`` (the GS1 variable-metric rule,
    including implied leading zeros for ``n > 6``) but exact; pounds are
    multiplied by the exact :data:`LB_TO_KG` factor.
    """
    precision = int(element.ai[3])
    weight = Decimal(element.value).scaleb(-precision)
    if element.unit == "lb":
        weight *= LB_TO_KG
    return weight


def summarize(scans: list[str], target_unit: str) -> list[dict]:
    """Aggregate scans into ordered ``(GTIN, batch)`` weight groups.

    Group order follows each group key's first appearance in ``scans``.
    The returned dicts carry ``gtin``, ``batch``, ``count``, ``unit`` and
    ``total``, where ``total`` is already converted to ``target_unit`` and
    rounded to six decimal places with banker's rounding.
    """
    # Insertion order preserves the first-appearance order of the groups.
    groups: dict[tuple[str, str | None], list] = {}

    for index, scan in enumerate(scans):
        gtin, batch, weight_kg = _extract(index, scan)
        key = (gtin, batch)
        if key not in groups:
            groups[key] = [0, Decimal("0")]
        groups[key][0] += 1
        groups[key][1] += weight_kg

    results = []
    with localcontext() as ctx:
        # kg -> lb is a non-terminating division; keep ample guard digits
        # before the final six-decimal quantize.
        ctx.prec = 50
        for (gtin, batch), (count, total_kg) in groups.items():
            if target_unit == "lb":
                total = total_kg / LB_TO_KG
            else:
                total = total_kg
            total = total.quantize(SIX_PLACES, rounding=ROUND_HALF_EVEN)
            results.append(
                {
                    "gtin": gtin,
                    "batch": batch,
                    "count": count,
                    "unit": target_unit,
                    "total": total,
                }
            )
    return results
