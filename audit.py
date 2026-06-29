"""
audit.py — Structured JSON audit log for Provenance Guard.
All attribution decisions, appeals, and certificates are persisted here.
"""

import json
import os
from datetime import datetime, timezone
from threading import Lock

LOG_FILE = os.environ.get("AUDIT_LOG_FILE", "audit_log.json")
_lock = Lock()


def _load() -> list:
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _save(entries: list):
    with open(LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2, default=str)


def log_classification(
    content_id: str,
    creator_id: str,
    attribution: str,
    confidence: float,
    llm_score: float,
    stylometric_score: float,
    burstiness_score: float,
    label_variant: str,
    content_type: str = "text",
    text_excerpt: str = "",
    insufficient_length: bool = False,
) -> dict:
    entry = {
        "entry_type": "classification",
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attribution": attribution,
        "confidence": round(confidence, 4),
        "signal_scores": {
            "llm_score": round(llm_score, 4),
            "stylometric_score": round(stylometric_score, 4),
            "burstiness_score": round(burstiness_score, 4),
        },
        "label_variant": label_variant,
        "status": "classified",
        "content_type": content_type,
        "text_excerpt": text_excerpt[:120] if text_excerpt else "",
        "insufficient_length": insufficient_length,
        "appeal": None,
        "certificate": None,
    }
    with _lock:
        entries = _load()
        entries.append(entry)
        _save(entries)
    return entry


def log_appeal(content_id: str, creator_reasoning: str) -> dict | None:
    with _lock:
        entries = _load()
        for entry in entries:
            if entry.get("content_id") == content_id and entry.get("entry_type") == "classification":
                entry["status"] = "under_review"
                entry["appeal"] = {
                    "appeal_reasoning": creator_reasoning,
                    "appealed_at": datetime.now(timezone.utc).isoformat(),
                    "original_confidence": entry["confidence"],
                    "original_attribution": entry["attribution"],
                }
                _save(entries)
                return entry
    return None


def log_certificate(content_id: str, certificate_id: str, verification_method: str) -> dict | None:
    with _lock:
        entries = _load()
        for entry in entries:
            if entry.get("content_id") == content_id and entry.get("entry_type") == "classification":
                entry["status"] = "verified_human"
                entry["certificate"] = {
                    "certificate_id": certificate_id,
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                    "verification_method": verification_method,
                }
                _save(entries)
                return entry
    return None


def get_log(limit: int = 50) -> list:
    entries = _load()
    return entries[-limit:]


def get_by_content_id(content_id: str) -> dict | None:
    entries = _load()
    for entry in reversed(entries):
        if entry.get("content_id") == content_id:
            return entry
    return None


def get_appeals() -> list:
    entries = _load()
    return [e for e in entries if e.get("status") == "under_review"]


def get_analytics() -> dict:
    entries = _load()
    classifications = [e for e in entries if e.get("entry_type") == "classification"]
    total = len(classifications)
    if total == 0:
        return {
            "total_submissions": 0,
            "ai_count": 0,
            "human_count": 0,
            "uncertain_count": 0,
            "ai_ratio": 0,
            "human_ratio": 0,
            "uncertain_ratio": 0,
            "appeal_count": 0,
            "appeal_rate": 0,
            "verified_human_count": 0,
            "avg_confidence": 0,
            "avg_llm_score": 0,
            "avg_stylometric_score": 0,
            "avg_burstiness_score": 0,
            "content_type_breakdown": {},
        }

    ai_count = sum(1 for e in classifications if e.get("attribution") == "likely_ai")
    human_count = sum(1 for e in classifications if e.get("attribution") == "likely_human")
    uncertain_count = sum(1 for e in classifications if e.get("attribution") == "uncertain")
    appeal_count = sum(1 for e in classifications if e.get("appeal") is not None)
    verified_count = sum(1 for e in classifications if e.get("status") == "verified_human")

    confidences = [e["confidence"] for e in classifications if "confidence" in e]
    llm_scores = [e["signal_scores"]["llm_score"] for e in classifications if "signal_scores" in e]
    stylo_scores = [e["signal_scores"]["stylometric_score"] for e in classifications if "signal_scores" in e]
    burst_scores = [e["signal_scores"]["burstiness_score"] for e in classifications if "signal_scores" in e]

    content_types = {}
    for e in classifications:
        ct = e.get("content_type", "text")
        content_types[ct] = content_types.get(ct, 0) + 1

    return {
        "total_submissions": total,
        "ai_count": ai_count,
        "human_count": human_count,
        "uncertain_count": uncertain_count,
        "ai_ratio": round(ai_count / total, 3),
        "human_ratio": round(human_count / total, 3),
        "uncertain_ratio": round(uncertain_count / total, 3),
        "appeal_count": appeal_count,
        "appeal_rate": round(appeal_count / total, 3),
        "verified_human_count": verified_count,
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0,
        "avg_llm_score": round(sum(llm_scores) / len(llm_scores), 3) if llm_scores else 0,
        "avg_stylometric_score": round(sum(stylo_scores) / len(stylo_scores), 3) if stylo_scores else 0,
        "avg_burstiness_score": round(sum(burst_scores) / len(burst_scores), 3) if burst_scores else 0,
        "content_type_breakdown": content_types,
    }
