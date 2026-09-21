"""Lightweight comment sentiment using VADER (no GPU, no model download)."""
from __future__ import annotations

from typing import Iterable, Optional

_analyzer = None
_tried = False


def _get_analyzer():
    global _analyzer, _tried
    if not _tried:
        _tried = True
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

            _analyzer = SentimentIntensityAnalyzer()
        except Exception:  # library missing: degrade gracefully
            _analyzer = None
    return _analyzer


def analyze_comments(comments: Iterable[str], max_n: int = 100) -> Optional[dict]:
    """Aggregate sentiment of up to ``max_n`` comments, or None if unavailable."""
    analyzer = _get_analyzer()
    texts = [c.strip() for c in comments if c and c.strip()][:max_n]
    if analyzer is None or not texts:
        return None
    scores = [analyzer.polarity_scores(t)["compound"] for t in texts]
    n = len(scores)
    pos = sum(1 for s in scores if s >= 0.05)
    neg = sum(1 for s in scores if s <= -0.05)
    return {
        "n": n,
        "mean": round(sum(scores) / n, 3),
        "positive": round(pos / n, 3),
        "negative": round(neg / n, 3),
        "neutral": round((n - pos - neg) / n, 3),
    }
