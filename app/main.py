"""FastAPI application exposing the GS1 element-string parser."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .gs1 import GS1ParseError, parse
from .summary import ScanFailure, summarize

app = FastAPI(
    title="GS1 Element String Parser",
    version="1.3.0",
    description=(
        "Parses GS1 input into structured, ordered AI elements. Accepts raw "
        "scan strings (FNC1 / ASCII 29 separated) and GS1 Digital Link URIs "
        "(http/https, AI/value pairs in path and query string). Supports AI "
        "01 (GTIN), 10 (batch/lot), 17 (expiry date), 21 (serial number) and "
        "the weight family 310n (net weight, kg) / 320n (net weight, lb), "
        "where n is 0-9 implied decimal places followed by a fixed 6-digit "
        "value; weight elements additionally carry decimal_value and unit. "
        "POST /weight-summary groups multiple scans by GTIN + batch and sums "
        "their net weights exactly (Decimal, 1 lb = 0.45359237 kg)."
    ),
)


class ParseRequest(BaseModel):
    data: str = Field(
        ...,
        title="Raw scan string or GS1 Digital Link URI",
        description=(
            "Either a raw GS1 element string as scanned (variable-length "
            "fields separated by ASCII 29, encoded in JSON as \u001d), or a "
            "GS1 Digital Link URI beginning with http:// or https://. "
            "Digital Link values are percent-decoded as UTF-8; path segments "
            "must form AI/value pairs; each AI may appear only once across "
            "path and query string."
        ),
        examples=[
            "01095011015300031725013110ABC12321SN0001",
            "https://id.gs1.org/01/09501101530003/17/250131?10=ABC123&21=SN0001",
        ],
    )


class Element(BaseModel):
    ai: str = Field(description="Application identifier, e.g. '01'")
    name: str = Field(description="Short element name, e.g. 'GTIN'")
    value: str = Field(
        description="Field value without AI and separators (raw digits for "
        "310n/320n, before applying the implied decimal point)"
    )
    offset: int = Field(description="Byte offset of the AI within the input")
    decimal_value: float | None = Field(
        default=None,
        description=(
            "310n/320n only: the 6-digit value interpreted with n implied "
            "decimal places. Absent for all other AIs."
        ),
    )
    unit: str | None = Field(
        default=None,
        description='310n/320n only: measurement unit, "kg" (310n) or "lb" (320n). '
        "Absent for all other AIs.",
    )


class ParseResponse(BaseModel):
    elements: list[Element]


class WeightSummaryRequest(BaseModel):
    scans: list[str] = Field(
        ...,
        title="Raw scan strings and/or GS1 Digital Link URIs",
        description=(
            "Each item is parsed like /parse input. Every item must contain "
            "exactly one AI 01 (GTIN), at most one AI 10 (batch/lot) and "
            "exactly one weight AI (310n kg or 320n lb)."
        ),
        examples=[
            [
                "0109501101530003310300251310LOT-A",
                "0109501101530003320200251310LOT-A",
                "01095011015300033103002513",
            ],
        ],
    )
    target_unit: Literal["kg", "lb"] = Field(
        default="kg",
        title="Unit for the per-group totals",
        description='"kg" (default) or "lb"; pounds convert at 1 lb = 0.45359237 kg.',
    )


class WeightGroup(BaseModel):
    gtin: str = Field(description="GTIN (AI 01 value) grouping the scans")
    batch: str | None = Field(
        default=None,
        description="Batch/lot (AI 10 value); null for scans without AI 10",
    )
    count: int = Field(description="Number of scans aggregated into this group")
    unit: Literal["kg", "lb"] = Field(description="Unit of total (the request target_unit)")
    total: Decimal = Field(
        description=(
            "Summed net weight in unit, rebuilt from the raw six-digit "
            "values with Decimal and rounded to exactly six decimal places "
            "using half-even (banker's) rounding. Serialized as a JSON "
            "string to preserve precision (no float)."
        )
    )


class WeightSummaryResponse(BaseModel):
    target_unit: Literal["kg", "lb"] = Field(description="Unit of every group total")
    groups: list[WeightGroup]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/parse", response_model=ParseResponse, response_model_exclude_none=True)
def parse_scan(req: ParseRequest) -> ParseResponse:
    try:
        elements = parse(req.data)
    except GS1ParseError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc
    return ParseResponse(elements=[Element(**vars(e)) for e in elements])


@app.post("/weight-summary", response_model=WeightSummaryResponse)
def weight_summary(req: WeightSummaryRequest) -> WeightSummaryResponse:
    try:
        groups = summarize(req.scans, req.target_unit)
    except ScanFailure as failure:
        detail = {"index": failure.index, "error": failure.cause.to_dict()}
        raise HTTPException(status_code=422, detail=detail) from failure.cause
    return WeightSummaryResponse(
        target_unit=req.target_unit,
        groups=[WeightGroup(**group) for group in groups],
    )
