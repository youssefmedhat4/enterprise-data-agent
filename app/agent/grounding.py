import re
from decimal import Decimal, InvalidOperation

from app.contracts.analytics import GroundedClaim, Scalar
from app.errors import GroundingFailureError


class GroundingValidator:
    def validate(
        self,
        *,
        answer: str,
        claims: list[GroundedClaim],
        rows: list[dict[str, object]],
    ) -> list[GroundedClaim]:
        if not rows and claims:
            raise GroundingFailureError()
        if rows and not claims:
            raise GroundingFailureError()

        for claim in claims:
            for evidence in claim.evidence:
                if evidence.row_index >= len(rows):
                    raise GroundingFailureError()
                row = rows[evidence.row_index]
                if evidence.field not in row or not _values_equal(
                    evidence.value,
                    row[evidence.field],
                ):
                    raise GroundingFailureError()

        supported_numbers = {
            normalized
            for row in rows
            for value in row.values()
            if (normalized := _normalized_number(value)) is not None
        }
        answer_numbers = {
            normalized
            for raw in re.findall(r"(?<![\w-])-?\d[\d,]*(?:\.\d+)?(?![\w-])", answer)
            if (normalized := _normalized_number(raw)) is not None
        }
        if not answer_numbers.issubset(supported_numbers):
            raise GroundingFailureError()
        return claims


def _values_equal(expected: Scalar, actual: object) -> bool:
    expected_number = _normalized_number(expected)
    actual_number = _normalized_number(actual)
    if expected_number is not None and actual_number is not None:
        return expected_number == actual_number
    if isinstance(expected, str) and not isinstance(actual, str):
        return expected == str(actual)
    return expected == actual


def _normalized_number(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value)).normalize()
    if isinstance(value, str) and re.fullmatch(r"[-+]?\$?[\d,]+(?:\.\d+)?", value.strip()):
        normalized = value.strip().replace("$", "").replace(",", "")
        try:
            return Decimal(normalized).normalize()
        except InvalidOperation:
            return None
    return None
