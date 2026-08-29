"""Feedback detection.

The user rates the previous answer by replying with a thumb or a short word. Only a
message that consists *entirely* of such a token counts, so "1 more question" stays a
question and is answered by the LLM as usual.
"""

from __future__ import annotations

from app.domain.enums import Feedback

THUMBS_UP = "\U0001f44d"
THUMBS_DOWN = "\U0001f44e"

POSITIVE_TOKENS = frozenset({THUMBS_UP, "1", "+", "y", "yes", "ok", "okay", "good", "useful"})
NEGATIVE_TOKENS = frozenset({THUMBS_DOWN, "0", "-", "n", "no", "bad", "useless", "wrong"})

# Emoji presentation selector and the five skin-tone modifiers: "👍🏽" must read as "👍".
_EMOJI_MODIFIERS = str.maketrans(
    "", "", "️︎\U0001f3fb\U0001f3fc\U0001f3fd\U0001f3fe\U0001f3ff"
)


def normalize_feedback_token(body: str) -> str:
    """Reduce a message to the token it would be classified by."""
    return body.strip().translate(_EMOJI_MODIFIERS).strip().strip(".!").casefold()


def classify_feedback(body: str) -> Feedback | None:
    """Return the rating, or ``None`` when the message is an ordinary question."""
    token = normalize_feedback_token(body)
    if not token:
        return None
    if token in POSITIVE_TOKENS:
        return Feedback.POSITIVE
    if token in NEGATIVE_TOKENS:
        return Feedback.NEGATIVE
    return None
