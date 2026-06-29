# Provenance Guard — Planning Document

## Architecture

### Submission Flow

```
POST /submit
    │
    ▼
Input Validation (text, creator_id, optional: content_type)
    │
    ├──► Signal 1: LLM Classification (Groq llama-3.3-70b-versatile)
    │         Returns: ai_probability (0.0–1.0), reasoning string
    │
    ├──► Signal 2: Stylometric Heuristics
    │         Returns: stylometric_score (0.0–1.0)
    │         Measures: sentence length variance, type-token ratio,
    │                   punctuation density, lexical formality
    │
    ├──► Signal 3 (Ensemble): Burstiness / Perplexity Proxy
    │         Returns: burstiness_score (0.0–1.0)
    │         Measures: sentence length burstiness, rare word density,
    │                   paragraph structural variance
    │
    ▼
Confidence Scorer
    │   Weighted average: LLM(0.50) + Stylometric(0.30) + Burstiness(0.20)
    │   Returns: combined confidence (0.0–1.0)
    │
    ▼
Transparency Label Generator
    │   confidence > 0.75 → HIGH_AI label
    │   confidence < 0.35 → HIGH_HUMAN label
    │   0.35–0.75        → UNCERTAIN label
    │
    ▼
Audit Logger → writes structured JSON entry
    │
    ▼
JSON Response → { content_id, attribution, confidence, label,
                  signal_scores, status }
```

### Appeal Flow

```
POST /appeal
    │
    ▼
Validate content_id + creator_reasoning
    │
    ▼
Lookup original classification in audit log
    │
    ▼
Update status → "under_review"
    │
    ▼
Append appeal entry to audit log (appeal_reasoning, timestamp)
    │
    ▼
JSON Response → { content_id, status: "under_review", message }
```

### Provenance Certificate Flow

```
POST /certify
    │
    ▼
Require: content_id + verification_method (declaration, metadata, or process_description)
    │
    ▼
Check: content must be classified as human OR under_review
    │
    ▼
Issue certificate → { certificate_id, verified_at, verification_method }
    │
    ▼
Update content status → "verified_human"
    │
    ▼
Audit log entry with certificate details
```

---

## Detection Signals

### Signal 1: LLM Classification (Groq)

**What it measures:** Semantic and stylistic coherence. The LLM evaluates whether the text reads as human or AI-generated based on holistic patterns — overuse of hedging language ("it is important to note"), transition phrases ("furthermore," "additionally"), lack of personal voice, unnaturally balanced sentence structure, and topic cohesion that's too smooth.

**Output:** A float between 0.0 (clearly human) and 1.0 (clearly AI), plus a brief reasoning string.

**Blind spots:**
- Highly polished human writing (academic papers, professional reports) can score high.
- Lightly edited AI output may score low if the human edits introduce irregularity.
- Short texts (<50 words) give the LLM insufficient signal.

**Weight:** 0.50 (highest — captures semantic nuance neither heuristic can)

---

### Signal 2: Stylometric Heuristics

**What it measures:** Statistical properties of the writing's structure. AI text tends toward uniformity; human writing is messier.

Metrics computed:
- **Sentence length variance:** Standard deviation of sentence lengths. AI text is more uniform → low variance → higher AI score.
- **Type-token ratio (TTR):** Unique words / total words. AI uses broader vocabulary more evenly; humans repeat words and use idioms → lower TTR in humans.
- **Punctuation density:** Commas, em-dashes, ellipses per sentence. Human writers use punctuation more variably and stylistically.
- **Formality score:** Ratio of formal markers (passive voice, nominalization) to informal ones (contractions, first-person). AI skews formal.

**Output:** Weighted combination of 4 sub-metrics → float 0.0–1.0.

**Blind spots:**
- Formal human writing (academic, legal) scores high even when human.
- AI writing prompted to "be casual" or "write like a human" may score low.
- Very short poems with simple vocabulary may falsely score as AI.

**Weight:** 0.30

---

### Signal 3: Burstiness / Perplexity Proxy (Ensemble Stretch Feature)

**What it measures:** Human writing is "bursty" — alternates between short punchy sentences and long complex ones. AI writing maintains a narrower rhythm. This signal also captures rare word usage: humans reach for unusual words in emotionally resonant moments; AI distributes rare words more evenly.

Metrics:
- **Burstiness index:** Ratio of std_dev to mean of sentence lengths. Humans > 0.4 typically; AI < 0.3.
- **Rare word density distribution:** Whether rare words cluster (human) or spread evenly (AI).
- **Paragraph length variance:** Whether paragraph structure is irregular (human) or consistent (AI).

**Output:** Float 0.0–1.0 (higher = more AI-like).

**Blind spots:**
- Stream-of-consciousness human writing (journaling, tweets) can appear bursty but short paragraphs may not trigger.
- AI with explicit style instructions ("vary your sentence length") can beat this signal.

**Weight:** 0.20

---

## Uncertainty Representation

### Score Thresholds

| Score Range | Label Category | Meaning |
|---|---|---|
| 0.75 – 1.00 | HIGH_AI | System is confident this is AI-generated |
| 0.60 – 0.74 | LEAN_AI | Signals lean AI but uncertainty is real |
| 0.35 – 0.59 | UNCERTAIN | System cannot confidently classify |
| 0.20 – 0.34 | LEAN_HUMAN | Signals lean human but uncertainty is real |
| 0.00 – 0.19 | HIGH_HUMAN | System is confident this is human-written |

For the transparency label, LEAN_AI and LEAN_HUMAN collapse into UNCERTAIN to avoid misleading users with marginal confidence. This reflects the asymmetry hint: false positives (labeling human work as AI) are worse than false negatives.

### Score Calibration Approach

A score of 0.50 means: "The signals are in genuine disagreement, or all signals are near the midpoint — the system cannot determine attribution." A score of 0.95 means: "All three signals agree strongly that this is AI-generated." Scores are not calibrated to a statistical model but to interpretable thresholds tied to the label variants above.

---

## Transparency Label Variants

### HIGH_CONFIDENCE_AI (confidence ≥ 0.75)

```
⚠️ Likely AI-Generated
Our analysis found strong indicators that this content may have been
generated by an AI tool. This label is shown when multiple detection
signals agree with high confidence.

Confidence: HIGH  |  Score: {score}

If you wrote this yourself, you can submit an appeal below.
Appeals are reviewed by our team within 48 hours.
```

### UNCERTAIN (confidence 0.35–0.74)

```
❓ Attribution Unclear
Our system analyzed this content but could not confidently determine
whether it was written by a person or generated by an AI tool.
This may reflect a unique writing style, mixed-origin content,
or the limits of our detection.

Confidence: UNCERTAIN  |  Score: {score}

If you are the human author, you can submit an appeal to have
this reviewed by our team.
```

### HIGH_CONFIDENCE_HUMAN (confidence < 0.35)

```
✅ Appears Human-Written
Our analysis found strong indicators that this content was written
by a person. Multiple signals consistent with human authorship
were detected.

Confidence: HIGH  |  Score: {score}
```

---

## Appeals Workflow

**Who can appeal:** Any creator who submitted the content (identified by creator_id). The endpoint accepts any content_id + reasoning without authentication for this prototype.

**What they provide:** `content_id` (from the original submission response) and `creator_reasoning` (free text, min 10 characters).

**What the system does:**
1. Looks up the original entry in the audit log by content_id.
2. Updates `status` from `"classified"` to `"under_review"`.
3. Appends an appeal record to the audit log with: `appeal_reasoning`, `appealed_at` timestamp, `original_confidence`, `original_attribution`.
4. Returns confirmation JSON with the content_id and new status.

**What a human reviewer sees in the appeal queue (GET /appeals):**
- List of all entries with `status: "under_review"`, sorted by appeal time.
- Each entry shows: original content excerpt (first 100 chars), original score, all signal scores, creator reasoning, timestamp of appeal.

---

## Anticipated Edge Cases

1. **Formal academic human writing:** A scholar writing in dense, passive-voice academic prose will trigger high stylometric AI scores. Signal 2 (formality ratio) and Signal 3 (low burstiness) will both misfire. The LLM may also be fooled by the uniformity. Mitigation: the uncertain zone is wide (0.35–0.75) and the label explicitly notes "unique writing style."

2. **AI text with deliberate errors introduced:** A user who pastes AI output and manually adds typos, informal phrases, or contractions will confuse all three signals. The stylometric signal will drop; the LLM may pick up residual patterns. Expected result: UNCERTAIN label, which is honest.

3. **Very short text (<30 words):** Taglines, one-liners, and micro-poems have insufficient signal for meaningful classification. All three signals will return near-midpoint scores. Mitigation: return a special `"insufficient_length"` warning alongside an UNCERTAIN label.

4. **Non-native English speakers:** Formal, rule-following English from non-native speakers can resemble AI text in stylometric measures. This is the most serious false-positive risk. The system's appeal flow is the intended mitigation; the label text explicitly acknowledges this case.

---

## AI Tool Plan

### Milestone 3 (Submission Endpoint + Signal 1)
- **Spec sections provided:** Detection Signals (Signal 1), Architecture diagram
- **Request:** "Generate a Flask app skeleton with POST /submit and GET /log routes. Also generate a `groq_signal()` function that sends text to Groq llama-3.3-70b-versatile and returns an ai_probability float and reasoning string."
- **Verification:** Run function directly on 3 test inputs (clear AI, clear human, borderline). Check that output is a float in [0,1] and reasoning is a non-empty string.

### Milestone 4 (Signal 2 + Signal 3 + Confidence Scoring)
- **Spec sections provided:** Detection Signals (Signal 2 and 3), Uncertainty Representation, Architecture diagram
- **Request:** "Generate `stylometric_signal()` computing sentence length variance, TTR, punctuation density, and formality score combined into a 0–1 float. Generate `burstiness_signal()` computing burstiness index and rare word density. Generate `combine_scores()` using weights 0.50/0.30/0.20."
- **Verification:** Test all 4 sample inputs from project spec. Confirm scores differ meaningfully. Print individual signal scores to verify each contributes.

### Milestone 5 (Production Layer)
- **Spec sections provided:** Transparency Label Variants, Appeals Workflow, Architecture diagram
- **Request:** "Generate `generate_label()` that maps a confidence float to one of three label strings per these exact thresholds and variant texts. Generate POST /appeal endpoint that updates status and logs the appeal."
- **Verification:** Submit inputs that hit each of three label zones. Test appeal with a known content_id and verify GET /log shows status update.

---

## Multi-Modal Support Plan (Stretch)

For image descriptions and structured metadata, a second content pipeline accepts:
- `content_type: "image_description"` — a text description of an image (alt-text style)
- `content_type: "metadata"` — JSON metadata blob (EXIF-style: camera model, software, timestamps)

Image description pipeline: Uses LLM signal only (stylometrics don't apply well to alt-text). Returns lower-confidence result with a note explaining reduced signal reliability.

Metadata pipeline: Checks for AI-generated software tags (e.g., "Stable Diffusion," "DALL-E," "Midjourney" in software field), creation/modification timestamp consistency, and missing fields common in AI-generated images (no GPS, no camera model). Returns structured result with `metadata_flags` array.
