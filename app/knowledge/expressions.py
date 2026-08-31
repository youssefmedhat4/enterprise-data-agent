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

from pydantic import BaseModel, ConfigDict, Field

#: How deep a proposal may nest. Enough for real business ratios, shallow
#: enough that evaluation cannot be made expensive by a crafted proposal.
MAX_EXPRESSION_DEPTH = 6


class ExpressionError(RuntimeError):
    """Raised when an expression is malformed or references unknown metrics."""


class StrictNode(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricRef(StrictNode):
    """A reference to another certified metric, by key."""

    kind: Literal["metric"] = "metric"
    metric_key: str = Field(min_length=1)


class Literal_(StrictNode):
    """A numeric constant. Stored as a string so decimals stay exact."""

    kind: Literal["literal"] = "literal"
    value: Decimal


class BinaryOp(StrictNode):
    kind: Literal["binary"] = "binary"
    operator: Literal["add", "subtract", "multiply", "divide"]
    left: ExpressionNode
    right: ExpressionNode


type ExpressionNode = MetricRef | Literal_ | BinaryOp

BinaryOp.model_rebuild()


def referenced_metrics(node: ExpressionNode) -> set[str]:
    """Every certified metric key the expression depends on."""
    if isinstance(node, MetricRef):
        return {node.metric_key}
    if isinstance(node, BinaryOp):
        return referenced_metrics(node.left) | referenced_metrics(node.right)
    return set()


def depth(node: ExpressionNode) -> int:
    if isinstance(node, BinaryOp):
        return 1 + max(depth(node.left), depth(node.right))
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

    left = evaluate(node.left, measures)
    right = evaluate(node.right, measures)
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
    return f"({describe(node.left)} {symbol} {describe(node.right)})"
