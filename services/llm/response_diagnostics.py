"""Numeric response-shape diagnostics. Never retain parts or their contents."""
from __future__ import annotations


class ResponseDiagnostics:
    def __init__(self):
        self.counts = dict(responses=0, candidates=0, parts=0, text_parts=0, visible_text_parts=0,
                           thought_parts=0, other_parts=0, blocked_ratings=0)
        self.unambiguous = True

    def observe(self, response):
        self.counts["responses"] += 1
        candidates = getattr(response, "candidates", None) or []
        self.counts["candidates"] += len(candidates)
        if len(candidates) > 1:
            self.unambiguous = False
        for candidate in candidates:
            reason = getattr(candidate, "finish_reason", None)
            reason = getattr(reason, "value", reason)
            if reason not in {None, "STOP", "FINISH_REASON_UNSPECIFIED"}:
                self.unambiguous = False
            for rating in getattr(candidate, "safety_ratings", None) or []:
                if getattr(rating, "blocked", None) is True:
                    self.counts["blocked_ratings"] += 1
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                self.counts["parts"] += 1
                if getattr(part, "text", None) is not None:
                    self.counts["text_parts"] += 1
                    if part.text.strip() and getattr(part, "thought", None) is not True:
                        self.counts["visible_text_parts"] += 1
                if getattr(part, "thought", None) is True:
                    self.counts["thought_parts"] += 1
                # Future/unknown part fields conservatively prevent a retry.
                fields = getattr(part, "model_fields_set", None)
                if fields is None:
                    fields = set(vars(part))
                non_text = fields - {"text", "thought", "thought_signature"}
                if any(getattr(part, key, None) is not None for key in non_text):
                    self.counts["other_parts"] += 1
                elif getattr(part, "thought_signature", None) and not getattr(part, "text", None):
                    self.counts["other_parts"] += 1

    def attach(self, error):
        error.diagnostics = dict(self.counts)
        error.empty_retry_safe = (
            error.kind == "empty_response" and error.reason == "STOP"
            and self.unambiguous and self.counts["candidates"] > 0
            and not any(self.counts[key] for key in ("visible_text_parts", "thought_parts", "other_parts", "blocked_ratings"))
        )
