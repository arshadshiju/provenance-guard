"""
app.py — Provenance Guard Flask API

Endpoints:
  POST /submit          — Submit content for attribution analysis
  POST /appeal          — Contest a classification
  POST /certify         — Earn a Provenance Certificate (verified human)
  GET  /log             — View recent audit log entries
  GET  /appeals         — View all pending appeal queue
  GET  /analytics       — Analytics dashboard data
  GET  /health          — Health check
"""

import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import audit
import labels as label_gen
import signals

load_dotenv()

app = Flask(__name__)

# ─────────────────────────────────────────────
# Rate Limiting
# ─────────────────────────────────────────────
# Reasoning:
#   - A real writer submits maybe 1–5 pieces per day, occasionally bursting to 10.
#   - 10/minute prevents rapid scripted flooding while allowing legit interactive use.
#   - 100/day is generous for any single user while blocking bulk abuse.
#   - The /appeal endpoint is less abuse-prone but still limited to 30/hour.
# ─────────────────────────────────────────────

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

MIN_TEXT_LENGTH = 30  # characters — below this, classification is unreliable


# ─────────────────────────────────────────────
# POST /submit
# ─────────────────────────────────────────────

@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    """
    Accept a piece of content for attribution analysis.

    Request body (JSON):
      text         (str, required)  — the content to analyze
      creator_id   (str, required)  — identifier for the submitting creator
      content_type (str, optional)  — "text" (default), "image_description", "metadata"
      metadata     (dict, optional) — required when content_type == "metadata"

    Response:
      content_id, attribution, confidence, signal_scores, label, status
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    text = data.get("text", "").strip()
    creator_id = data.get("creator_id", "").strip()
    content_type = data.get("content_type", "text").strip().lower()

    if not text:
        return jsonify({"error": "Field 'text' is required and cannot be empty."}), 400
    if not creator_id:
        return jsonify({"error": "Field 'creator_id' is required."}), 400

    content_id = str(uuid.uuid4())
    insufficient_length = len(text) < MIN_TEXT_LENGTH

    # ── Multi-modal: metadata content type ──
    if content_type == "metadata":
        metadata_dict = data.get("metadata", {})
        if not metadata_dict:
            return jsonify({"error": "Field 'metadata' (dict) is required for content_type=metadata."}), 400

        meta_score, meta_flags = signals.metadata_signal(metadata_dict)
        attribution = signals.attribution_from_confidence(meta_score)
        transparency_label = label_gen.generate_label(meta_score, attribution)

        entry = audit.log_classification(
            content_id=content_id,
            creator_id=creator_id,
            attribution=attribution,
            confidence=meta_score,
            llm_score=meta_score,
            stylometric_score=meta_score,
            burstiness_score=meta_score,
            label_variant=transparency_label["variant"],
            content_type="metadata",
            text_excerpt=str(metadata_dict)[:120],
        )

        return jsonify({
            "content_id": content_id,
            "content_type": "metadata",
            "attribution": attribution,
            "confidence": meta_score,
            "metadata_flags": meta_flags,
            "signal_scores": {
                "metadata_score": meta_score,
                "flags": meta_flags,
            },
            "label": transparency_label,
            "label_text": label_gen.format_label_text(transparency_label),
            "status": "classified",
            "timestamp": entry["timestamp"],
        }), 200

    # ── Standard text / image_description pipeline ──
    if insufficient_length:
        # Still run signals but flag uncertainty
        llm_score, llm_reasoning = 0.5, "Text too short for reliable LLM analysis."
        stylo_score, stylo_detail = 0.5, {}
        burst_score, burst_detail = 0.5, {}
        confidence = 0.5
        attribution = "uncertain"
    else:
        # Signal 1: LLM
        llm_score, llm_reasoning = signals.groq_signal(text)

        # Signal 2: Stylometric
        stylo_score, stylo_detail = signals.stylometric_signal(text)

        # Signal 3: Burstiness
        burst_score, burst_detail = signals.burstiness_signal(text)

        # Ensemble
        confidence = signals.combine_scores(llm_score, stylo_score, burst_score)
        attribution = signals.attribution_from_confidence(confidence)

    transparency_label = label_gen.generate_label(confidence, attribution, insufficient_length)

    entry = audit.log_classification(
        content_id=content_id,
        creator_id=creator_id,
        attribution=attribution,
        confidence=confidence,
        llm_score=llm_score,
        stylometric_score=stylo_score,
        burstiness_score=burst_score,
        label_variant=transparency_label["variant"],
        content_type=content_type,
        text_excerpt=text[:120],
        insufficient_length=insufficient_length,
    )

    return jsonify({
        "content_id": content_id,
        "content_type": content_type,
        "attribution": attribution,
        "confidence": confidence,
        "signal_scores": {
            "llm_score": llm_score,
            "llm_reasoning": llm_reasoning,
            "stylometric_score": stylo_score,
            "stylometric_detail": stylo_detail,
            "burstiness_score": burst_score,
            "burstiness_detail": burst_detail,
            "weights": signals.SIGNAL_WEIGHTS,
        },
        "label": transparency_label,
        "label_text": label_gen.format_label_text(transparency_label),
        "status": "classified",
        "timestamp": entry["timestamp"],
        "insufficient_length_warning": insufficient_length,
    }), 200


# ─────────────────────────────────────────────
# POST /appeal
# ─────────────────────────────────────────────

@app.route("/appeal", methods=["POST"])
@limiter.limit("30 per hour")
def appeal():
    """
    Contest a classification result.

    Request body:
      content_id        (str, required) — from the original /submit response
      creator_reasoning (str, required) — why the creator believes the result is wrong
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    content_id = data.get("content_id", "").strip()
    reasoning = data.get("creator_reasoning", "").strip()

    if not content_id:
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if len(reasoning) < 10:
        return jsonify({"error": "Field 'creator_reasoning' must be at least 10 characters."}), 400

    updated = audit.log_appeal(content_id, reasoning)
    if not updated:
        return jsonify({"error": f"No classification found for content_id '{content_id}'."}), 404

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": (
            "Your appeal has been received and logged. Our team will review "
            "this classification within 48 hours. Thank you for helping us improve."
        ),
        "original_attribution": updated["appeal"]["original_attribution"],
        "original_confidence": updated["appeal"]["original_confidence"],
        "appealed_at": updated["appeal"]["appealed_at"],
    }), 200


# ─────────────────────────────────────────────
# POST /certify  (Stretch: Provenance Certificate)
# ─────────────────────────────────────────────

@app.route("/certify", methods=["POST"])
@limiter.limit("20 per hour")
def certify():
    """
    Issue a Provenance Certificate to a creator who has completed a
    verification step attesting to human authorship.

    Request body:
      content_id           (str, required)
      verification_method  (str, required) — "declaration" | "process_description" | "metadata_proof"
      verification_detail  (str, required) — the creator's attestation or process description
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    content_id = data.get("content_id", "").strip()
    verification_method = data.get("verification_method", "").strip()
    verification_detail = data.get("verification_detail", "").strip()

    valid_methods = {"declaration", "process_description", "metadata_proof"}
    if not content_id:
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if verification_method not in valid_methods:
        return jsonify({"error": f"'verification_method' must be one of: {sorted(valid_methods)}"}), 400
    if len(verification_detail) < 20:
        return jsonify({"error": "Field 'verification_detail' must be at least 20 characters."}), 400

    # Check the content exists and isn't already verified
    existing = audit.get_by_content_id(content_id)
    if not existing:
        return jsonify({"error": f"No content found for content_id '{content_id}'."}), 404
    if existing.get("status") == "verified_human":
        return jsonify({
            "error": "This content already has a Provenance Certificate.",
            "certificate": existing.get("certificate"),
        }), 409

    # Only allow certification for human-leaning or under-review content
    allowed_statuses = {"classified", "under_review"}
    allowed_attributions = {"likely_human", "uncertain"}
    if existing.get("status") not in allowed_statuses:
        return jsonify({"error": "Content is not in a state that allows certification."}), 409
    if existing.get("attribution") == "likely_ai" and existing.get("status") != "under_review":
        return jsonify({
            "error": (
                "Content was classified as likely AI-generated. "
                "Please submit an appeal first before requesting a certificate."
            )
        }), 409

    certificate_id = f"PG-CERT-{str(uuid.uuid4())[:8].upper()}"
    updated = audit.log_certificate(content_id, certificate_id, verification_method)
    cert_label = label_gen.generate_certificate_label(certificate_id, verification_method)

    return jsonify({
        "content_id": content_id,
        "certificate_id": certificate_id,
        "status": "verified_human",
        "verified_at": updated["certificate"]["verified_at"],
        "verification_method": verification_method,
        "label": cert_label,
        "label_text": label_gen.format_label_text(cert_label),
        "message": (
            "Provenance Certificate issued. This content is now marked as "
            "Verified Human-Written on the platform."
        ),
    }), 200


# ─────────────────────────────────────────────
# GET /log
# ─────────────────────────────────────────────

@app.route("/log", methods=["GET"])
def get_log():
    """Return recent audit log entries as structured JSON."""
    limit = min(int(request.args.get("limit", 50)), 200)
    entries = audit.get_log(limit)
    return jsonify({
        "count": len(entries),
        "entries": entries,
    }), 200


# ─────────────────────────────────────────────
# GET /appeals
# ─────────────────────────────────────────────

@app.route("/appeals", methods=["GET"])
def get_appeals():
    """Return all content items currently under review."""
    items = audit.get_appeals()
    return jsonify({
        "count": len(items),
        "appeals": items,
    }), 200


# ─────────────────────────────────────────────
# GET /analytics  (Stretch: Analytics Dashboard)
# ─────────────────────────────────────────────

@app.route("/analytics", methods=["GET"])
def analytics():
    """
    Returns detection patterns, appeal rate, and additional metrics
    for the analytics dashboard.
    """
    data = audit.get_analytics()
    return jsonify(data), 200


# ─────────────────────────────────────────────
# GET /health
# ─────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "Provenance Guard",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "groq_key_set": bool(os.environ.get("GROQ_API_KEY")),
    }), 200


# ─────────────────────────────────────────────
# Error handlers
# ─────────────────────────────────────────────

@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({
        "error": "Rate limit exceeded. Please slow down and try again shortly.",
        "detail": str(e.description),
    }), 429


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found."}), 404


if __name__ == "__main__":
    app.run(debug=True, port=5000)
