"""Bounded arithmetic for derived metrics.

A derived metric such as `annual_payroll_per_active_employee` is expressed as a
small tree of typed nodes over *certified metric keys* and numeric literals.
There is deliberately no node that can carry SQL, a function name, a column, a
table, or Python. A model proposing a derived metric therefore cannot smuggle
execution into a definition: the worst it can propose is arithmetic over metrics
that already exist and are already certified.

Evaluation happens after each dependency has been computed by the governed
layer at the requested grain, so this never touches a database.

Division by zero yields None rather than raising or returning zero. A null
reads as "not defined for this row", which is what a per-employee figure for a
department with no employees actually means; zero would be a false statement
and an exception would fail the whole result over one empty group.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: How deep a proposal may nest. Enough for real business ratios, shallow
#: enough that evaluation cannot be made expensive by a crafted proposal.
MAX_EXPRESSION_DEPTH = 6


class ExpressionError(RuntimeError):
    """Raised when an expression is malformed or references unknown metrics."""


#: Which shape a node is, is decided by the fields it carries: an operator
#: makes it an operation, a metric key makes it a reference, a value makes it a
#: literal. The `kind` tag restates that and carries nothing of its own.
#:
#: Models spell that tag however they like -- "metric_reference",
#: "MetricExpression" -- and an otherwise correct proposal was being thrown
#: away over the spelling. Matching a list of synonyms only postpones the
#: problem, so the kind is read from the shape and the tag is corrected to
#: agree. Content stays strict: metric keys, operators and values are
#: validated exactly as before, and a node whose fields fit no shape is still
#: refused.
_KIND_BY_FIELD = (
    ("operator", "binary"),
    ("metric_key", "metric"),
    ("value", "literal"),
)


def kind_from_shape(data: Mapping[str, object]) -> str | None:
    """The node kind implied by which fields are present, if any."""
    for field, kind in _KIND_BY_FIELD:
        if field in data:
            return kind
    return None


class StrictNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _kind_follows_shape(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        inferred = kind_from_shape(data)
        if inferred is None or data.get("kind") == inferred:
            return data
        return {**data, "kind": inferred}


class MetricRef(StrictNode):
    """A reference to another certified metric, by key."""

    kind: Literal["metric"] = "metric"
    metric_key: str = Field(min_length=1)


class Literal_(StrictNode):
    """A numeric constant. Stored as a string so decimals stay exact."""

    kind: Literal["literal"] = "literal"
    value: Decimal


class BinaryOp(StrictNode):
    """One arithmetic step over two operands.

    The operands are declared nullable purely so the generated JSON schema is
    one a structured-output provider will accept. Vertex refuses a schema whose
    recursion is reachable only through required fields -- "a ref loop of
    required fields was found" -- even though this recursion terminates at a
    metric reference or a literal. Declaring them required made every candidate
    the background worker tried to generate fail with a 400, silently, for
    every proposal: the tests all use a scripted model, so nothing noticed.

    Nullable in the schema is not nullable in the contract. The validator below
    refuses a node that is actually missing an operand, so a half-built
    expression can never be stored or evaluated.
    """

    kind: Literal["binary"] = "binary"
    operator: Literal["add", "subtract", "multiply", "divide"]
    left: ExpressionNode | None = None
    right: ExpressionNode | None = None

    @model_validator(mode="after")
    def _requires_both_operands(self) -> BinaryOp:
        if self.left is None or self.right is None:
            raise ValueError(
                "A binary operation needs both a left and a right operand."
            )
        return self


type ExpressionNode = MetricRef | Literal_ | BinaryOp

BinaryOp.model_rebuild()


def operands(node: BinaryOp) -> tuple[ExpressionNode, ExpressionNode]:
    """Both operands of a validated node.

    Unreachable for anything that passed validation; it exists so the nullable
    schema declaration does not leak `None` handling into every caller.
    """
    if node.left is None or node.right is None:  # pragma: no cover - validated
        raise ExpressionError("A binary operation is missing an operand.")
    return node.left, node.right


def referenced_metrics(node: ExpressionNode) -> set[str]:
    """Every certified metric key the expression depends on."""
    if isinstance(node, MetricRef):
        return {node.metric_key}
    if isinstance(node, BinaryOp):
        left, right = operands(node)
        return referenced_metrics(left) | referenced_metrics(right)
    return set()


def depth(node: ExpressionNode) -> int:
    if isinstance(node, BinaryOp):
        left, right = operands(node)
        return 1 + max(depth(left), depth(right))
    return 1


def validate_expression(
    node: ExpressionNode,
    *,
    available_metric_keys: set[str],
    metric_key: str | None = None,
) -> None:
    """Check an expression before it can become a certified definition.

    Refuses unknown dependencies, self-reference, and excessive nesting. Cycles
    across several derived metrics are caught by `assert_acyclic`, which needs
    the whole dependency graph rather than one expression.
    """
    if depth(node) > MAX_EXPRESSION_DEPTH:
        raise ExpressionError(
            f"Expression nests deeper than {MAX_EXPRESSION_DEPTH} levels."
        )
    referenced = referenced_metrics(node)
    if not referenced:
        raise ExpressionError(
            "A derived metric must reference at least one certified metric."
        )
    if metric_key is not None and metric_key in referenced:
        raise ExpressionError(f"Metric {metric_key!r} cannot reference itself.")
    unknown = referenced - available_metric_keys
    if unknown:
        raise ExpressionError(
            f"Expression references metrics that are not certified: {sorted(unknown)}."
        )


def assert_acyclic(
    metric_key: str,
    node: ExpressionNode,
    *,
    dependencies_of: Mapping[str, set[str]],
) -> None:
    """Refuse a definition that would close a dependency cycle.

    `dependencies_of` maps every existing derived metric to what it depends on.
    Adding `metric_key` must not make it reachable from itself, or evaluation
    would never terminate.
    """
    graph = {key: set(value) for key, value in dependencies_of.items()}
    graph[metric_key] = referenced_metrics(node)

    visiting: set[str] = set()
    settled: set[str] = set()

    def visit(key: str) -> None:
        if key in settled:
            return
        if key in visiting:
            raise ExpressionError(
                f"Metric {metric_key!r} would create a circular dependency via {key!r}."
            )
        visiting.add(key)
        for dependency in graph.get(key, set()):
            visit(dependency)
        visiting.discard(key)
        settled.add(key)

    visit(metric_key)


def evaluate(
    node: ExpressionNode, measures: Mapping[str, Decimal | None]
) -> Decimal | None:
    """Evaluate one row's measures.

    Returns None when any input is missing or when a division has a zero
    divisor. A null propagates rather than being coerced, so a derived figure is
    absent exactly where it is undefined instead of quietly reading as zero.
    """
    if isinstance(node, Literal_):
        return node.value
    if isinstance(node, MetricRef):
        return measures.get(node.metric_key)

    left_node, right_node = operands(node)
    left = evaluate(left_node, measures)
    right = evaluate(right_node, measures)
    if left is None or right is None:
        return None
    try:
        if node.operator == "add":
            return left + right
        if node.operator == "subtract":
            return left - right
        if node.operator == "multiply":
            return left * right
        if right == 0:
            return None
        return left / right
    except (DivisionByZero, InvalidOperation, ArithmeticError):
        return None


def describe(node: ExpressionNode) -> str:
    """Human-readable form for a reviewer. Never executed."""
    if isinstance(node, Literal_):
        return str(node.value)
    if isinstance(node, MetricRef):
        return node.metric_key
    symbol = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}[
        node.operator
    ]
    left, right = operands(node)
    return f"({describe(left)} {symbol} {describe(right)})"
