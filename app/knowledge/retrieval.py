"""Hybrid, datasource-scoped metric retrieval.

The system previously routed by scanning a question for literal configured
aliases. That is brittle in the way that matters most: a question phrased in
ordinary business language — "how much money does the organization commit to
employee base compensation each year?" — contains none of the configured
aliases, so it fell through to ad-hoc SQL even though a certified metric
answered it exactly.

Retrieval here combines two independent signals over rich metric documents:

* **Vector similarity** against an embedding of the document, which is what
  carries paraphrase.
* **Lexical overlap** against the document's informative terms, which anchors
  the result when wording does align and keeps a purely vector-driven near-miss
  from dominating.

Aliases survive only as ordinary words inside the document. They contribute the
same weight as any other concept term and never decide a route by themselves.

Scores are fused with a weighted sum rather than reciprocal-rank fusion because
callers need a comparable magnitude to threshold on, not just an ordering: the
planner must be able to say "nothing here is close enough" and fall back.

Retrieval is a *suggestion* mechanism. It ranks candidates; it never authorizes
them and never decides the final plan. The caller filters to authorized metrics
before ranking, and a planner selects from what survives.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from uuid import UUID

from app.embeddings.gateway import EmbeddingGateway, EmbeddingVector
from app.knowledge.metrics import RegisteredMetric

#: Words that carry no topical signal. Kept deliberately small: an aggressive
#: list would strip domain words that matter.
_STOPWORDS = frozenset(
    [
        "a", "about", "across", "all", "an", "and", "are", "as", "at", "be", "by", "do",
        "does", "each", "every", "for", "from", "give", "how", "in", "into", "is", "it",
        "its", "many", "me", "much", "of", "on", "or", "our", "over", "per", "show", "tell",
        "that", "the", "their", "there", "these", "this", "to", "total", "up", "us", "was",
        "what", "when", "where", "which", "who", "with", "within", "you", "your"
    ]
)

_WORD = re.compile(r"[a-z0-9]+")

#: Vector similarity carries paraphrase, so it leads. Lexical overlap is a
#: corroborating signal rather than an equal partner.
_VECTOR_WEIGHT = 0.7
_LEXICAL_WEIGHT = 0.3


def tokenize(text: str) -> list[str]:
    return [word for word in _WORD.findall(text.casefold()) if word not in _STOPWORDS]


@dataclass(frozen=True, slots=True)
class MetricCandidate:
    """One retrieved metric with the evidence for why it ranked."""

    metric: RegisteredMetric
    score: float
    vector_similarity: float
    lexical_score: float

    @property
    def metric_key(self) -> str:
        return self.metric.metric_key


@dataclass(frozen=True, slots=True)
class _IndexedDocument:
    metric_key: str
    embedding: EmbeddingVector
    term_frequencies: dict[str, float]


class MetricRetriever:
    """Ranks certified metrics for one datasource against a question.

    An instance holds an index per datasource. Nothing is shared between
    datasources: `index()` replaces one datasource's documents and cannot touch
    another's, so datasource A's metrics can never be returned for B.
    """

    def __init__(self, embeddings: EmbeddingGateway) -> None:
        self._embeddings = embeddings
        self._documents: dict[UUID, list[_IndexedDocument]] = {}
        self._document_frequency: dict[UUID, dict[str, int]] = {}

    @property
    def embedding_provider(self) -> str:
        """Recorded with a reindex so incompatible vectors stay identifiable."""
        return self._embeddings.provider

    @property
    def embedding_model(self) -> str:
        return self._embeddings.model

    @property
    def embedding_dimension(self) -> int:
        return self._embeddings.dimension

    async def index(
        self, data_source_id: UUID, metrics: list[RegisteredMetric]
    ) -> None:
        """Build the retrieval index for one datasource.

        Only certified metrics are indexed. A proposed or rejected definition is
        invisible to retrieval, which is what stops an unapproved candidate from
        influencing a live answer.
        """
        certified = [metric for metric in metrics if metric.is_governed_runtime_visible]
        if not certified:
            self._documents[data_source_id] = []
            self._document_frequency[data_source_id] = {}
            return

        texts = [metric.retrieval_document() for metric in certified]
        vectors = await self._embeddings.embed(texts)

        frequency: dict[str, int] = {}
        documents: list[_IndexedDocument] = []
        for metric, vector, text in zip(certified, vectors, texts, strict=True):
            tokens = tokenize(text)
            counts: dict[str, float] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0.0) + 1.0
            for token in counts:
                frequency[token] = frequency.get(token, 0) + 1
            documents.append(
                _IndexedDocument(
                    metric_key=metric.metric_key,
                    embedding=vector,
                    term_frequencies=counts,
                )
            )
        self._documents[data_source_id] = documents
        self._document_frequency[data_source_id] = frequency

    async def retrieve(
        self,
        *,
        data_source_id: UUID,
        question: str,
        authorized_metrics: list[RegisteredMetric],
        limit: int = 5,
    ) -> list[MetricCandidate]:
        """Rank `authorized_metrics` for `question` within one datasource.

        `authorized_metrics` must already be filtered to what the caller may
        see. Retrieval never widens that set, so it cannot become a channel for
        discovering metrics the caller is not entitled to know exist.
        """
        documents = self._documents.get(data_source_id, [])
        if not documents or not authorized_metrics:
            return []

        allowed = {
            metric.metric_key: metric
            for metric in authorized_metrics
            if metric.data_source_id == data_source_id
            and metric.is_governed_runtime_visible
        }
        if not allowed:
            return []

        query_vector = (await self._embeddings.embed([question]))[0]
        query_tokens = tokenize(question)
        frequency = self._document_frequency.get(data_source_id, {})
        total_documents = max(len(documents), 1)

        candidates: list[MetricCandidate] = []
        for document in documents:
            metric = allowed.get(document.metric_key)
            if metric is None:
                continue
            # Refuses to compare vectors from different models, so a re-embedding
            # with a changed model fails loudly instead of silently degrading.
            query_vector.assert_comparable(document.embedding)
            similarity = _cosine(query_vector, document.embedding)
            lexical = _lexical_score(
                query_tokens, document.term_frequencies, frequency, total_documents
            )
            candidates.append(
                MetricCandidate(
                    metric=metric,
                    score=_VECTOR_WEIGHT * similarity + _LEXICAL_WEIGHT * lexical,
                    vector_similarity=similarity,
                    lexical_score=lexical,
                )
            )
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.metric_key))
        return candidates[:limit]


def _cosine(left: EmbeddingVector, right: EmbeddingVector) -> float:
    dot = sum(a * b for a, b in zip(left.values, right.values, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left.values))
    right_norm = math.sqrt(sum(value * value for value in right.values))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _lexical_score(
    query_tokens: list[str],
    term_frequencies: dict[str, float],
    document_frequency: dict[str, int],
    total_documents: int,
) -> float:
    """IDF-weighted overlap, normalized to roughly 0..1.

    A term appearing in every metric document ("department") should barely move
    the score; a term appearing in one ("payroll") should move it a lot. Plain
    overlap counting gets this backwards, which is how alias matching produced
    confident wrong routes.
    """
    if not query_tokens:
        return 0.0
    matched = 0.0
    possible = 0.0
    for token in set(query_tokens):
        idf = math.log((total_documents + 1) / (document_frequency.get(token, 0) + 1)) + 1.0
        possible += idf
        if token in term_frequencies:
            matched += idf
    return matched / possible if possible else 0.0
