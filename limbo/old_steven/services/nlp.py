"""
Lightweight NLP for rule-based chat.

Uses spaCy lemmatization when available; falls back to simple regex
tokenisation if spaCy is not installed or the model is missing.

Rule format (stored as JSON in AgentProfile.system_prompt):

    {
      "fallback": "I'm not sure I understood. Try: pricing, support, …",
      "rules": [
        {
          "intent": "pricing",
          "required_any": ["price", "cost", "fee", "plan"],
          "required_all": [],
          "response": "Which plan are you asking about?"
        },
        {
          "intent": "cancel_subscription",
          "required_any": ["cancel", "stop", "unsubscribe"],
          "required_all": ["subscription"],
          "response": "I can help with cancellation."
        }
      ]
    }

Scoring:
  - required_all must be a strict subset of the lemma set (all must match).
  - Score = number of required_any lemmas found in the input.
  - The rule with the highest score wins; ties broken by first rule listed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Lazy spaCy loader
# ---------------------------------------------------------------------------

_nlp = None          # None = not yet tried; False = unavailable
_SPACY_MODEL = "en_core_web_sm"


def _get_nlp():
    global _nlp
    if _nlp is None:
        try:
            import spacy
            _nlp = spacy.load(_SPACY_MODEL)
        except (ImportError, OSError):
            _nlp = False
    return _nlp if _nlp is not False else None


def lemmatize(text: str) -> set[str]:
    """
    Return the set of lemmas for *text*.
    Punctuation and whitespace tokens are skipped.
    Stop words are KEPT because intent keywords like 'cancel', 'help', 'stop'
    can themselves be stop words in some corpora.
    """
    nlp = _get_nlp()
    if nlp is not None:
        doc = nlp(text.lower())
        return {tok.lemma_ for tok in doc if not tok.is_punct and not tok.is_space}
    # Fallback: lowercase word splitting (no real lemmatisation)
    return set(re.findall(r"\b[a-z]+\b", text.lower()))


def spacy_available() -> bool:
    return _get_nlp() is not None


# ---------------------------------------------------------------------------
# Rule data structure
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    intent: str
    required_any: set[str] = field(default_factory=set)
    required_all: set[str] = field(default_factory=set)
    response: str = ""

    def score(self, words: set[str]) -> int | None:
        """
        Return a match score (>= 1) or None if the rule doesn't match.

        - required_all is a hard gate: all must be present.
        - score = required_any hits + len(required_all) bonus.
          The bonus means rules that require specific co-occurrence (e.g.
          "cancel" + "subscription") beat loose single-keyword matches.
        """
        if self.required_all and not (self.required_all <= words):
            return None
        if self.required_any:
            s = len(words & self.required_any)
            if s == 0:
                return None
            return s + len(self.required_all)   # bonus for required_all matches
        # Rule with empty required_any acts as a catch-all (lowest priority)
        return len(self.required_all) if self.required_all else 1


# ---------------------------------------------------------------------------
# Intent matcher
# ---------------------------------------------------------------------------

class IntentMatcher:
    def __init__(self, rules: list[Rule], fallback: str):
        self.rules = rules
        self.fallback = fallback

    def match(self, text: str) -> tuple[str | None, str]:
        """
        Return (intent_name, response_text).
        intent_name is None when falling back.
        """
        words = lemmatize(text)

        best_rule: Rule | None = None
        best_score = 0

        for rule in self.rules:
            s = rule.score(words)
            if s is not None and s > best_score:
                best_score = s
                best_rule = rule

        if best_rule is None:
            return None, self.fallback
        return best_rule.intent, best_rule.response


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

DEFAULT_FALLBACK = (
    "I'm not sure I understood. "
    "I can help with pricing, support, or subscription questions. "
    "Type 'help' for built-in commands."
)


def parse_system_prompt(system_prompt: str) -> tuple[IntentMatcher | None, str]:
    """
    Try to parse system_prompt as a rule-set JSON.
    Returns (IntentMatcher, fallback) on success, or (None, raw_prompt) if the
    prompt is a plain text system prompt (not a rule set).
    """
    try:
        data = json.loads(system_prompt)
    except (json.JSONDecodeError, TypeError):
        return None, system_prompt

    if not isinstance(data, dict) or "rules" not in data:
        return None, system_prompt

    rules = []
    for r in data.get("rules", []):
        rules.append(Rule(
            intent=r.get("intent", "unknown"),
            required_any=set(r.get("required_any", [])),
            required_all=set(r.get("required_all", [])),
            response=r.get("response", ""),
        ))

    fallback = data.get("fallback", DEFAULT_FALLBACK)
    return IntentMatcher(rules, fallback), fallback
