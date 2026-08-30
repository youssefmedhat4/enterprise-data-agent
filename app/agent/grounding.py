import logging
import re
from decimal import Decimal, InvalidOperation

from app.contracts.analytics import GroundedClaim, Scalar
from app.errors import GroundingFailureError

logger = logging.getLogger(__name__)

_PROSE_NUMERAL = re.compile(r"(?<![\w-])-?\d[\d,]*(?:\.\d+)?(?![\w-])")
_ROW_COUNT_LEFT = re.compile(
    r"(?:\b(?:result|query|response|output)\s+"
    r"(?:contains?|contained|returns?|returned|has|includes?|included|produces?|produced)"
    r"|\b(?:across|in)\s+(?:all\s+)?)\s*$",
    re.IGNORECASE,
)
_ROW_COUNT_RIGHT = re.compile(
    r"^\s+(?:result\s+)?(?:rows?|records?|results?|departments?|categories?)\b",
    re.IGNORECASE,
)
_RANK_LEFT = re.compile(
    r"\b(?:rank(?:ed|ing)?|position(?:ed)?|place(?:d)?|top)"
    r"(?:\s+(?:at|as|number))?\s*$",
    re.IGNORECASE,
)
_RANK_RIGHT = re.compile(
    r"^\s+(?:rank|ranking|position|place)\b",
    re.IGNORECASE,
)


class GroundingValidator:
    """Verifies that an answer is supported by the executed query result.

    Grounding rests on two rules of very different strength.

    The **primary boundary** is structured evidence: every claim cites a row
    index, a field, and a value, and each must match the real result exactly.
    This is what actually prevents invented business facts, and it is unchanged.

    The **secondary sweep** looks at numerals in prose that no claim cited. A
    numeral is accepted only when it is a result value, the exact row count in
    explicit result-shape wording, or a bounded integer in explicit rank wording.
    Small business quantities are never trusted merely because they are less than
    the row count.
    """

    def validate(
        self,
        *,
        answer: str,
        claims: list[GroundedClaim],
        rows: list[dict[str, object]],
    ) -> list[GroundedClaim]:
        if not rows and claims:
            raise self._fail(
                "GROUNDING_CLAIMS_ON_EMPTY_RESULT",
                claim_count=len(claims),
                row_count=0,
            )
        if rows and not claims:
            raise self._fail(
                "GROUNDING_MISSING_CLAIMS",
                claim_count=0,
                row_count=len(rows),
            )

        # Primary boundary: every cited piece of evidence must match the result.
        for claim_index, claim in enumerate(claims):
            for evidence_index, evidence in enumerate(claim.evidence):
                if evidence.row_index >= len(rows):
                    raise self._fail(
                        "GROUNDING_EVIDENCE_ROW_OUT_OF_RANGE",
                        claim_index=claim_index,
                        evidence_index=evidence_index,
                        row_count=len(rows),
                    )
                row = rows[evidence.row_index]
                if evidence.field not in row:
                    raise self._fail(
                        "GROUNDING_EVIDENCE_FIELD_MISSING",
                        claim_index=claim_index,
                        evidence_index=evidence_index,
                    )
                if not _values_equal(evidence.value, row[evidence.field]):
                    raise self._fail(
                        "GROUNDING_EVIDENCE_VALUE_MISMATCH",
                        claim_index=claim_index,
                        evidence_index=evidence_index,
                    )

        # Secondary sweep: numerals in prose that no claim vouched for.
        supported = {
            normalized
            for row in rows
            for value in row.values()
            if (normalized := _normalized_number(value)) is not None
        }
        row_count = len(rows)
        for numeric_index, match in enumerate(_PROSE_NUMERAL.finditer(answer)):
            raw = match.group(0)
            number = _normalized_number(raw)
            if number is None or number in supported:
                continue
            if _is_supported_structural_numeral(
                number=number,
                answer=answer,
                start=match.start(),
                end=match.end(),
                row_count=row_count,
            ):
                continue
            raise self._fail(
                "GROUNDING_UNSUPPORTED_NUMERIC_CLAIM",
                numeric_index=numeric_index,
                row_count=row_count,
            )
        return claims

    def _fail(self, reason: str, **diagnostics: int) -> GroundingFailureError:
        detail = " ".join(f"{key}={value}" for key, value in sorted(diagnostics.items()))
        logger.warning("grounding rejected answer: code=%s %s", reason, detail)
        return GroundingFailureError(reason=reason, detail=detail or None)


def _is_supported_structural_numeral(
    *,
    number: Decimal,
    answer: str,
    start: int,
    end: int,
    row_count: int,
) -> bool:
    if number != number.to_integral_value():
        return False
    integer = int(number)
    left = answer[max(0, start - 80) : start]
    right = answer[end : min(len(answer), end + 40)]
    if integer == row_count and _ROW_COUNT_LEFT.search(left) and _ROW_COUNT_RIGHT.search(right):
        return True
    return 1 <= integer <= row_count and bool(
        _RANK_LEFT.search(left) or _RANK_RIGHT.search(right)
    )


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
