"""End-to-end tests for the /weight-summary endpoint."""

from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.summary import LB_TO_KG, SIX_PLACES

client = TestClient(app)

GS = "\x1d"
GTIN_OK = "09501101530003"  # valid Mod-10 check digit
GTIN_2 = "00012345678905"   # another valid Mod-10 GTIN


def weight_summary(scans, target_unit=None):
    payload = {"scans": scans}
    if target_unit is not None:
        payload["target_unit"] = target_unit
    return client.post("/weight-summary", json=payload)


def kg_scan(gtin, batch, value, precision=3):
    ai = f"310{precision}"
    return f"01{gtin}10{batch}{GS}{ai}{value}"


def lb_scan(gtin, batch, value, precision=2):
    ai = f"320{precision}"
    return f"01{gtin}10{batch}{GS}{ai}{value}"


# ---------------------------------------------------------------- happy path

def test_groups_by_gtin_and_batch_in_first_appearance_order():
    # same GTIN, no batch -> its own group
    scans = [
        kg_scan(GTIN_OK, "LOT-A", "002513"),  # 2.513 kg
        kg_scan(GTIN_2, "LOT-B", "001000"),   # 1.000 kg
        kg_scan(GTIN_OK, "LOT-A", "005026"),  # 5.026 kg
        f"01{GTIN_OK}3103000001",             # 0.001 kg, no batch
    ]
    resp = weight_summary(scans)
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["target_unit"] == "kg"
    groups = body["groups"]
    assert [(g["gtin"], g["batch"]) for g in groups] == [
        (GTIN_OK, "LOT-A"),
        (GTIN_2, "LOT-B"),
        (GTIN_OK, None),
    ]
    assert [g["count"] for g in groups] == [2, 1, 1]
    assert [g["unit"] for g in groups] == ["kg", "kg", "kg"]
    assert [g["total"] for g in groups] == ["7.539000", "1.000000", "0.001000"]


def test_total_decimal_reconstruction_ignores_float_decimal_value():
    # n=9: 000001 -> 0.000000001 kg; a float round-trip cannot be summed
    # ten times into exactly 0.00000001, Decimal can.
    scans = [f"01{GTIN_OK}3109000001"] * 10
    resp = weight_summary(scans)
    assert resp.status_code == 200
    group = resp.json()["groups"][0]
    assert group["count"] == 10
    assert group["total"] == "0.000000"


def test_conversion_lb_scans_to_kg_uses_exact_factor():
    # 3202 002513 = 25.13 lb -> kg
    resp = weight_summary([f"01{GTIN_OK}3202002513"], target_unit="kg")
    assert resp.status_code == 200
    total = resp.json()["groups"][0]["total"]
    expected = (Decimal("002513").scaleb(-2) * LB_TO_KG).quantize(
        SIX_PLACES, rounding="ROUND_HALF_EVEN"
    )
    assert total == str(expected)
    assert total == "11.398776"


def test_kg_scans_converted_to_lb_target_unit():
    # 2.513 kg / 0.45359237 lb/kg
    resp = weight_summary([f"01{GTIN_OK}3103002513"], target_unit="lb")
    assert resp.status_code == 200
    group = resp.json()["groups"][0]
    assert group["unit"] == "lb"
    expected = (
        Decimal("2.513") / LB_TO_KG
    ).quantize(SIX_PLACES, rounding="ROUND_HALF_EVEN")
    assert group["total"] == str(expected)
    assert group["total"] == "5.540217"


def test_default_target_unit_is_kg():
    resp = weight_summary([f"01{GTIN_OK}3103002513"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_unit"] == "kg"
    assert body["groups"][0]["total"] == "2.513000"


def test_mixed_kg_and_lb_scans_in_same_group():
    # 2.513 kg + 25.13 lb = 2.513 + 11.39877626... = 13.91177626... kg
    resp = weight_summary(
        [
            kg_scan(GTIN_OK, "A", "002513"),
            lb_scan(GTIN_OK, "A", "002513"),
        ],
        target_unit="kg",
    )
    assert resp.status_code == 200
    group = resp.json()["groups"][0]
    assert group["count"] == 2
    assert group["total"] == "13.911776"


def test_six_decimal_half_even_rounding_ties():
    # 3107 000005 = 0.0000005 kg: the 7th decimal is exactly 5, a true tie
    # at six places that half-even (banker's) rounding resolves to the even
    # neighbour:
    one = [f"01{GTIN_OK}3107000005"]      # 0.0000005 -> 0.000000 (toward even)
    two = one * 2                         # 0.0000010 -> 0.000001 (exact)
    three = one * 3                       # 0.0000015 -> 0.000002 (toward even)
    four = one * 4                        # 0.0000020 -> 0.000002 (exact)
    five = one * 5                        # 0.0000025 -> 0.000002 (toward even)
    assert weight_summary(one).json()["groups"][0]["total"] == "0.000000"
    assert weight_summary(two).json()["groups"][0]["total"] == "0.000001"
    assert weight_summary(three).json()["groups"][0]["total"] == "0.000002"
    assert weight_summary(four).json()["groups"][0]["total"] == "0.000002"
    assert weight_summary(five).json()["groups"][0]["total"] == "0.000002"


def test_half_even_rounds_four_down():
    # 0.0000004 kg is below the tie at the 7th place -> 0.000000
    resp = weight_summary([f"01{GTIN_OK}3107000004"])
    assert resp.json()["groups"][0]["total"] == "0.000000"


def test_no_float_accumulation_repeated_tenth_kg():
    # float accumulation of 0.1 introduces error; Decimal keeps the total
    # exact so quantize never flips a boundary. 3101 000001 = 0.1 kg.
    scans = [kg_scan(GTIN_OK, "B", "000001", precision=1)] * 10
    resp = weight_summary(scans)
    assert resp.status_code == 200
    assert resp.json()["groups"][0]["total"] == "1.000000"


def test_n_zero_precision_reconstructed_as_integer():
    resp = weight_summary([f"01{GTIN_OK}10X{GS}3100012345"])  # 12345 kg
    assert resp.status_code == 200
    assert resp.json()["groups"][0]["total"] == "12345.000000"


def test_digital_link_scans_accepted():
    scan = f"https://id.gs1.org/01/{GTIN_OK}/10/LOT-A/3103/002513"
    resp = weight_summary([scan])
    assert resp.status_code == 200
    group = resp.json()["groups"][0]
    assert group == {
        "gtin": GTIN_OK,
        "batch": "LOT-A",
        "count": 1,
        "unit": "kg",
        "total": "2.513000",
    }


def test_other_ais_allowed_but_ignored():
    scan = f"01{GTIN_OK}1725013110ABC{GS}21SN0001{GS}3103002513"
    resp = weight_summary([scan])
    assert resp.status_code == 200
    group = resp.json()["groups"][0]
    assert group["gtin"] == GTIN_OK
    assert group["batch"] == "ABC"
    assert group["total"] == "2.513000"


def test_empty_scans_returns_empty_groups():
    resp = weight_summary([])
    assert resp.status_code == 200
    assert resp.json() == {"target_unit": "kg", "groups": []}


# ---------------------------------------------------------------- errors

def test_parser_error_reports_index_and_original_error():
    scans = [
        f"01{GTIN_OK}3103002513",
        "0109501101530004",  # bad check digit
        f"01{GTIN_OK}3103002513",
    ]
    resp = weight_summary(scans)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["index"] == 1
    assert detail["error"]["error"] == "INVALID_CHECK_DIGIT"
    assert detail["error"]["offset"] == 15
    assert detail["error"]["found"] == "4"


def test_missing_gtin():
    resp = weight_summary(["3103002513"])
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["index"] == 0
    assert detail["error"]["error"] == "MISSING_GTIN"
    assert "01" in detail["error"]["expected"]


def test_duplicate_gtin():
    resp = weight_summary([f"01{GTIN_OK}01{GTIN_OK}3103002513"])
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["index"] == 0
    assert detail["error"]["error"] == "DUPLICATE_AI"
    assert detail["error"]["offset"] == 16  # second AI 01 (2 AI chars + 14 digits)


def test_duplicate_batch():
    resp = weight_summary(
        [f"01{GTIN_OK}10A{GS}10B{GS}3103002513"]
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["index"] == 0
    assert detail["error"]["error"] == "DUPLICATE_AI"


def test_missing_weight():
    resp = weight_summary([f"01{GTIN_OK}10A{GS}17250131"])
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["index"] == 0
    assert detail["error"]["error"] == "MISSING_WEIGHT"
    assert "310n" in detail["error"]["expected"]


def test_two_weights_kg_and_lb_rejected():
    resp = weight_summary([f"01{GTIN_OK}31030025133203002513"])
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["index"] == 0
    assert detail["error"]["error"] == "DUPLICATE_WEIGHT"
    assert detail["error"]["found"] == "3203"


def test_two_weights_same_family_rejected():
    resp = weight_summary(
        [f"https://id.gs1.org/01/{GTIN_OK}/3103/002513?3101=012345"]
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["index"] == 0
    assert detail["error"]["error"] == "DUPLICATE_WEIGHT"


def test_error_index_points_at_later_scan():
    scans = [
        f"01{GTIN_OK}3103002513",
        f"01{GTIN_OK}3103002513",
        "not-a-valid-scan",  # starts with unsupported AI "no"
    ]
    resp = weight_summary(scans)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["index"] == 2
    assert detail["error"]["error"] in {"UNSUPPORTED_AI", "INVALID_AI"}


def test_invalid_target_unit_rejected():
    resp = weight_summary([f"01{GTIN_OK}3103002513"], target_unit="st")
    assert resp.status_code == 422


def test_missing_scans_field_rejected():
    resp = client.post("/weight-summary", json={})
    assert resp.status_code == 422


def test_non_string_scan_item_rejected():
    resp = client.post(
        "/weight-summary", json={"scans": [f"01{GTIN_OK}3103002513", 123]}
    )
    assert resp.status_code == 422
