import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.contracts.analytics import GroundedClaim, Scalar
from app.errors import GroundingFailureError

logger = logging.getLogger(__name__)

_PROSE_NUMERAL = re.compile(r"(?<![\w-])-?\d[\d,]*(?:\.\d+)?(?![\w-])")
#: Digit runs inside a result value, wherever they sit. Hyphens and letters
#: around them are part of a label, not a reason to ignore the number.
_VALUE_NUMERAL = re.compile(r"\d+(?:\.\d+)?")
_ROW_COUNT_LEFT = re.compile(
    r"(?:\b(?:result|query|response|output)\s+"
    r"(?:contains?|contained|returns?|returned|has|includes?|included|produces?|produced)"
    r"|\b(?:there\s+(?:are|were)|a\s+total\s+of|we\s+(?:have|found|identified))"
    r"|\b(?:across|in)\s+(?:all\s+)?)\s*$",
    re.IGNORECASE,
)
#: A plural noun within a few words of the numeral. The previous rule listed the
#: nouns it would accept -- "rows", "records", "departments", "categories" --
#: which is the demo database's vocabulary written into general code. On a
#: schema whose entities are employees or projects, a correct count of the rows
#: returned was rejected as an unsupported claim and a right answer was thrown
#: away. What makes such a numeral verifiable is that it equals the row count,
#: not which noun happens to follow it.
_ROW_COUNT_RIGHT = re.compile(
    r"^\s+(?:[A-Za-z][\w-]*\s+){0,3}[A-Za-z][\w-]*s\b",
    re.IGNORECASE,
)
#: Plural nouns that make a numeral an amount rather than a count of things.
_AMOUNT_NOUNS = frozenset(
    {"dollars", "euros", "pounds", "cents", "percent", "points", "times"}
)
_AMOUNT_PREFIX = re.compile(r"[$€£¥]\s*$")
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
        # Numerals *inside* result text -- "Project 003", "Q3 2024", "2024-02".
        # Naming such a row back to the user quotes the result; it does not
        # invent anything. Counting only wholly numeric values meant listing
        # the projects that matched a question failed as an unsupported claim.
        #
        # Harvested with a looser pattern than the prose scan below. The prose
        # guards against matching inside an identifier, which is right when
        # reading a sentence and wrong when reading data: a month label of
        # "2024-02" yielded neither 2024 nor 02, so every time-series answer
        # that named its year was rejected for quoting the result back.
        supported |= {
            number
            for row in rows
            for value in row.values()
            if isinstance(value, str)
            for fragment in _VALUE_NUMERAL.findall(value)
            if (number := _normalized_number(fragment)) is not None
        }
        row_count = len(rows)
        for numeric_index, match in enumerate(_PROSE_NUMERAL.finditer(answer)):
            raw = match.group(0)
            number = _normalized_number(raw)
            if number is None or number in supported:
                continue
            if _is_rounded_form_of_supported(number, raw, supported):
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


def _is_rounded_form_of_supported(
    number: Decimal, raw: str, supported: set[Decimal]
) -> bool:
    """Whether a real result value, rounded as written, gives this numeral.

    An average of 113571.428571 shown as 113,571.43 is presentation, not
    invention -- but it was rejected as an unsupported number, so an otherwise
    correct answer failed. Rounding cannot manufacture a figure: some value in
    the result still has to produce it at the stated precision.

    Only applied when the numeral has fewer decimal places than the value it
    matches; a numeral with *more* precision than anything in the result is
    still an invention.
    """
    _, _, fraction = raw.partition(".")
    places = len(fraction)
    if places == 0:
        # Whole numbers are handled by the row-count and rank rules. Accepting
        # them here would let any value round to a nearby integer.
        return False
    quantum = Decimal(1).scaleb(-places)
    return any(
        value.quantize(quantum, rounding=ROUND_HALF_UP) == number
        for value in supported
        if -value.as_tuple().exponent > places  # type: ignore[operator]
    )


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
    if integer == row_count and _is_row_count_wording(left, right):
        return True
    return 1 <= integer <= row_count and bool(
        _RANK_LEFT.search(left) or _RANK_RIGHT.search(right)
    )


def _is_row_count_wording(left: str, right: str) -> bool:
    """Whether this numeral is stated as how many things were returned.

    Deliberately does not require both sides to match. "There are 42 active
    employees" and "42 active employees have..." are the same claim about the
    same result, and demanding explicit result-shape wording on the left only
    ever accepted answers that sounded like a database talking about itself.

    The numeral still has to equal the row count exactly, which is what makes
    it verifiable; this only decides whether it is being used as a count.
    """
    if _AMOUNT_PREFIX.search(left):
        return False
    following = _ROW_COUNT_RIGHT.search(right)
    if following is not None:
        noun = following.group(0).split()[-1].casefold()
        return noun not in _AMOUNT_NOUNS
    return _ROW_COUNT_LEFT.search(left) is not None


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
