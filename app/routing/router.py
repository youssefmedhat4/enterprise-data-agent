from __future__ import annotations

import re
import unicodedata

from app.agent.context import AnalyticalContext
from app.metrics.catalog import GOVERNED_METRICS
from app.routing.contracts import QueryRoute, RouteDecision, RouteReasonCode

_ENGLISH_WRITE_TERMS = re.compile(
    r"\b(delete|insert|update|drop|alter|truncate|create\s+(?:user|table|database)|"
    r"grant|revoke|remove|erase|destroy)\b",
    re.IGNORECASE,
)
_ARABIC_WRITE_TERMS = (
    "احذف",
    "حذف",
    "امسح",
    "مسح",
    "عدل",
    "تعديل",
    "اسقط",
    "إسقاط",
    "انشئ مستخدم",
    "أنشئ مستخدم",
    "امنح صلاحية",
)
_FOLLOWUP_TERMS = (
    "what about",
    "how about",
    "do the same",
    "same for",
    "only ",
    "just ",
    "and last year",
    "ماذا عن",
    "نفس الشيء",
    "نفسها",
    "فقط",
)
_RAW_LOOKUP_TERMS = (
    "show every",
    "show each employee",
    "list ",
    "records",
    "rows",
    "details",
    "salary for each",
    "each employee salary",
    "اعرض كل",
    "قائمة",
    "سجلات",
    "تفاصيل",
)
_ADHOC_COMPOSITE_TERMS = (
    "average salary",
    "highest paid employee",
    "employee salary",
    "متوسط الراتب",
    "اعلي موظف راتبا",
)
_AGGREGATE_TERMS = (
    "total",
    "sum",
    "average",
    "avg",
    "count",
    "how many",
    "highest",
    "lowest",
    "top ",
    "by ",
    "per ",
    "اجمالي",
    "مجموع",
    "متوسط",
    "عدد",
    "حسب",
    "اعلى",
)
_INHERENT_METRIC_TERMS = (
    "headcount",
    "annual payroll",
    "annual base payroll",
    "net payroll",
    "invoice amount",
    "project margin",
    "margin",
    "budget utilization",
    "budget used",
    "هامش المشروع",
    "الميزانيه المستخدمه",
    "صافي الرواتب",
)


class DeterministicQueryRouter:
    """Route from catalog evidence without generating SQL or metric formulas."""

    def route(
        self,
        question: str,
        *,
        prior_context: AnalyticalContext | None = None,
        allowed_metric_ids: frozenset[str] | None = None,
    ) -> RouteDecision:
        normalized = normalize_text(question)
        if _has_write_intent(normalized):
            return RouteDecision(
                route=QueryRoute.BLOCK,
                confidence=1,
                reason_code=RouteReasonCode.WRITE_INTENT,
                block_reason="This analytics service does not permit data modification.",
            )

        if _is_followup(normalized):
            decision = self._followup_decision(prior_context)
            if _contains_unauthorized_metric(decision.metric_candidates, allowed_metric_ids):
                return _unauthorized_metric_decision(decision.metric_candidates)
            return decision

        matches = _metric_matches(normalized)
        if any(normalize_text(term) in normalized for term in _ADHOC_COMPOSITE_TERMS):
            return RouteDecision(
                route=QueryRoute.ADHOC_ANALYTICS,
                confidence=0.99,
                reason_code=RouteReasonCode.ADHOC_DEFAULT,
                metric_candidates=tuple(metric_id for metric_id, _ in matches),
            )
        if matches and _is_row_lookup(normalized) and not _has_aggregate_intent(normalized):
            return RouteDecision(
                route=QueryRoute.ADHOC_ANALYTICS,
                confidence=0.98,
                reason_code=RouteReasonCode.ROW_LEVEL_LOOKUP,
                metric_candidates=tuple(metric_id for metric_id, _ in matches),
            )

        if matches and _has_metric_calculation_intent(normalized):
            candidates = _select_metric_candidates(normalized, matches)
            if _contains_unauthorized_metric(candidates, allowed_metric_ids):
                return _unauthorized_metric_decision(candidates)
            if len(candidates) > 1:
                return RouteDecision(
                    route=QueryRoute.CLARIFY,
                    confidence=0.98,
                    reason_code=RouteReasonCode.MULTIPLE_METRICS_UNSUPPORTED,
                    metric_candidates=candidates,
                    clarification_reason=(
                        "The current governed path supports one metric per request."
                    ),
                    clarification_question="Which single governed metric should I calculate first?",
                )
            metric_id, matched_alias = candidates[0], _best_alias(matches, candidates[0])
            exact = normalized == matched_alias or normalized in {
                f"what is {matched_alias}",
                f"show {matched_alias}",
            }
            return RouteDecision(
                route=QueryRoute.GOVERNED_METRIC,
                confidence=1 if exact else 0.97,
                reason_code=(
                    RouteReasonCode.METRIC_EXACT_MATCH
                    if exact
                    else RouteReasonCode.METRIC_SEMANTIC_MATCH
                ),
                metric_candidates=(metric_id,),
            )

        return RouteDecision(
            route=QueryRoute.ADHOC_ANALYTICS,
            confidence=0.9,
            reason_code=(
                RouteReasonCode.ROW_LEVEL_LOOKUP
                if _is_row_lookup(normalized)
                else RouteReasonCode.ADHOC_DEFAULT
            ),
        )

    def _followup_decision(
        self,
        prior_context: AnalyticalContext | None,
    ) -> RouteDecision:
        if prior_context is None or prior_context.execution_route is None:
            return RouteDecision(
                route=QueryRoute.CLARIFY,
                confidence=1,
                reason_code=RouteReasonCode.FOLLOWUP_WITHOUT_CONTEXT,
                requires_prior_context=True,
                clarification_reason=(
                    "The request refers to an earlier analysis that is unavailable."
                ),
                clarification_question="Which analysis should I continue?",
            )
        route = QueryRoute(prior_context.execution_route)
        metric_candidates = (
            (prior_context.metric_query.metric,)
            if route == QueryRoute.GOVERNED_METRIC and prior_context.metric_query is not None
            else ()
        )
        return RouteDecision(
            route=route,
            confidence=1,
            reason_code=RouteReasonCode.FOLLOWUP_REFERENCE,
            metric_candidates=metric_candidates,
            requires_prior_context=True,
        )


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(character for character in value if not unicodedata.combining(character))
    for source, target in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        value = value.replace(source, target)
    value = re.sub(r"[^\w\s-]", " ", value, flags=re.UNICODE)
    return " ".join(value.replace("_", " ").split())


def _has_write_intent(normalized: str) -> bool:
    return bool(_ENGLISH_WRITE_TERMS.search(normalized)) or any(
        normalize_text(term) in normalized for term in _ARABIC_WRITE_TERMS
    )


def _is_followup(normalized: str) -> bool:
    return any(normalize_text(term) in normalized for term in _FOLLOWUP_TERMS)


def _is_row_lookup(normalized: str) -> bool:
    return any(normalize_text(term) in normalized for term in _RAW_LOOKUP_TERMS)


def _has_aggregate_intent(normalized: str) -> bool:
    return any(normalize_text(term) in normalized for term in _AGGREGATE_TERMS)


def _has_metric_calculation_intent(normalized: str) -> bool:
    return _has_aggregate_intent(normalized) or any(
        normalize_text(term) in normalized for term in _INHERENT_METRIC_TERMS
    ) or normalized.startswith(("what is ", "how much ", "ما هو ", "كم "))


def _metric_matches(normalized: str) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    for definition in GOVERNED_METRICS:
        for alias in (definition.id, *definition.aliases):
            normalized_alias = normalize_text(alias)
            if normalized_alias and normalized_alias in normalized:
                matches.append((definition.id, normalized_alias))
    return matches


def _select_metric_candidates(
    normalized: str,
    matches: list[tuple[str, str]],
) -> tuple[str, ...]:
    best_by_metric: dict[str, int] = {}
    for metric_id, alias in matches:
        best_by_metric[metric_id] = max(best_by_metric.get(metric_id, 0), len(alias))
    if len(best_by_metric) == 1:
        return tuple(best_by_metric)
    connector = any(token in normalized for token in (" and ", " & ", " و "))
    if connector:
        return tuple(best_by_metric)
    longest = max(best_by_metric.values())
    return tuple(metric_id for metric_id, length in best_by_metric.items() if length == longest)


def _best_alias(matches: list[tuple[str, str]], metric_id: str) -> str:
    return max((alias for candidate, alias in matches if candidate == metric_id), key=len)


def _contains_unauthorized_metric(
    candidates: tuple[str, ...],
    allowed_metric_ids: frozenset[str] | None,
) -> bool:
    return allowed_metric_ids is not None and any(
        metric_id not in allowed_metric_ids for metric_id in candidates
    )


def _unauthorized_metric_decision(candidates: tuple[str, ...]) -> RouteDecision:
    return RouteDecision(
        route=QueryRoute.BLOCK,
        confidence=1,
        reason_code=RouteReasonCode.UNAUTHORIZED_METRIC,
        metric_candidates=candidates,
        block_reason="The requested governed metric is not available to this identity.",
    )
