"""The price table (GLOSSARY.md), shared by the worker, which prices LLM spans, and ingest, which
prices experiment samples. Cost is computed once, on the server (ADR-0010)."""

import bisect
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

# Rows for `PriceTable.from_rows`, in this column order.
PRICES_SQL = (
    "SELECT model, effective_from, input_per_mtok, output_per_mtok, cache_read_per_mtok, "
    "cache_write_5m_per_mtok, cache_write_1h_per_mtok "
    "FROM model_prices ORDER BY model, effective_from"
)


class Usage(Protocol):
    """Token counts for one model. `input_tokens` excludes cached tokens."""

    @property
    def model(self) -> str | None: ...
    @property
    def input_tokens(self) -> int | None: ...
    @property
    def output_tokens(self) -> int | None: ...
    @property
    def cache_read_tokens(self) -> int | None: ...
    @property
    def cache_write_5m_tokens(self) -> int | None: ...
    @property
    def cache_write_1h_tokens(self) -> int | None: ...


@dataclass(frozen=True)
class Price:
    effective_from: datetime
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float | None = None
    cache_write_5m_per_mtok: float | None = None
    cache_write_1h_per_mtok: float | None = None


@dataclass
class PriceTable:
    by_model: dict[str, list[Price]] = field(default_factory=dict)  # each list sorted by date

    @classmethod
    def from_rows(cls, rows: Iterable[tuple]) -> "PriceTable":
        """Rows of `PRICES_SQL`; prices may come back as Decimal."""
        table = cls()
        for model, eff, *rates in rows:
            inp, out, read, w5m, w1h = (None if r is None else float(r) for r in rates)
            assert inp is not None and out is not None  # NOT NULL columns
            table.by_model.setdefault(model, []).append(Price(eff, inp, out, read, w5m, w1h))
        return table

    def cost(self, usage: Usage, at: datetime) -> float | None:
        """USD at the price in effect at `at`, or None when it can't be priced."""
        prices = self.by_model.get(usage.model or "")
        if not prices or usage.input_tokens is None or usage.output_tokens is None:
            return None
        i = bisect.bisect_right([p.effective_from for p in prices], as_utc(at)) - 1
        if i < 0:
            return None
        p = prices[i]
        total = usage.input_tokens * p.input_per_mtok + usage.output_tokens * p.output_per_mtok
        # input_tokens excludes cached tokens, so cache reads and writes are priced on top.
        for tokens, price in (
            (usage.cache_read_tokens, p.cache_read_per_mtok),
            (usage.cache_write_5m_tokens, p.cache_write_5m_per_mtok),
            (usage.cache_write_1h_tokens, p.cache_write_1h_per_mtok),
        ):
            if tokens:
                if price is None:
                    return None  # unknown beats wrong (ADR-0010)
                total += tokens * price
        return total / 1e6


def as_utc(t: datetime) -> datetime:
    """The SDK sends UTC; a timestamp without a zone is read as UTC too."""
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
