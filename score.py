import os, json, csv, glob, sys
from typing import Dict, Any, List

PAPERS_DIR   = "json_data"
POLICY_PATH  = "policy/checklist.json"
OUT_DIR      = "results"
REPORTS_DIR  = os.path.join(OUT_DIR, "reports")

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

DEFAULT_RUBRIC = {
    "criteria": [
        {"id": "real_time_transparency",        "name": "Real-Time Transparency",                                   "weight": 15},
        {"id": "explainability",                "name": "Explainability",                                            "weight": 15},
        {"id": "accountability",                "name": "Accountability",                                            "weight": 15},
        {"id": "human_oversight",               "name": "Human Oversight",                                           "weight": 10},
        {"id": "privacy",                       "name": "Privacy",                                                   "weight": 15},
        {"id": "data_protection",               "name": "Data Protection",                                           "weight": 15},
        {"id": "continuous_ethics_monitoring",  "name": "Continuous Ethical Monitoring (Lifecycle Governance)",      "weight": 15}
    ],
    "allow_partial_scoring": True,
    "partial_ratio": 0.5
}

def clamp(n, lo, hi):
    return max(lo, min(hi, n))

def _normalize_quotes(x) -> List[str]:
    if isinstance(x, list):
        return [str(i) for i in x if isinstance(i, (str, int, float))]
    if isinstance(x, (str, int, float)):
        return [str(x)]
    return []

def load_policy() -> Dict[str, Any]:
    if os.path.exists(POLICY_PATH):
        try:
            with open(POLICY_PATH, "r", encoding="utf-8") as f:
                policy = json.load(f)
        except Exception as e:
            print(f"[WARN] Failed to load policy '{POLICY_PATH}': {e}. Using DEFAULT_RUBRIC.")
            return DEFAULT_RUBRIC
        crit = policy.get("criteria", [])
        seen = set()
        valid = True
        for c in crit:
            cid = c.get("id")
            w = c.get("weight", 0)
            if not cid or cid in seen:
                print(f"[WARN] Invalid or duplicate criterion id: {cid}")
                valid = False
            seen.add(cid)
            try:
                w = int(w)
            except Exception:
                print(f"[WARN] Non-integer weight for {cid}: {w}")
                valid = False
            if w < 0:
                print(f"[WARN] Negative weight for {cid}: {w}")
                valid = False
        if not valid or not crit:
            print("[WARN] Policy invalid. Falling back to DEFAULT_RUBRIC.")
            return DEFAULT_RUBRIC
        return policy
    return DEFAULT_RUBRIC

policy = load_policy()
CRITERIA_LIST = policy["criteria"]

def compute_score(weight: int, ev: Dict[str, Any]) -> int:
    if not isinstance(ev, dict):
        return 0

    # Use presence confidence as a base multiplier
    pres_conf = float(ev.get("present_confidence", 0))
    if pres_conf <= 0:
        return 0

    # Quote quality average (scale 0 to 5)
    quote_qualities = ev.get("quote_quality") or []
    if quote_qualities:
        avg_quote_quality = sum(quote_qualities)/len(quote_qualities)
    else:
        avg_quote_quality = 0.0

    # Notes quality scale 0 to 5
    notes_quality = int(ev.get("notes_quality", 0))

    # Number of quotes (bonus factor)
    num_quotes = len(ev.get("quotes_or_pointers") or [])

    # Evidence type weighting (example)
    ev_types = ev.get("evidence_type") or []
    ev_type_factor = 1.0
    if "Empirical Data" in ev_types:
        ev_type_factor += 0.2
    if "Normative Claim" in ev_types:
        ev_type_factor += 0.1
    if "Case Study" in ev_types:
        ev_type_factor += 0.15

    # Compose final multiplier (capped at 1.0)
    multiplier = pres_conf
    multiplier *= (avg_quote_quality / 5.0)
    multiplier += (notes_quality / 10.0)  # up to +0.5
    multiplier += min(0.05 * num_quotes, 0.25)  # up to +0.25
    multiplier *= ev_type_factor
    multiplier = min(multiplier, 1.0)

    score = round(weight * multiplier)
    return clamp(score, 0, weight)

paper_files = sorted(glob.glob(os.path.join(PAPERS_DIR, "*.json")))
if not paper_files:
    print(f"No JSON files found in {PAPERS_DIR}.")
    sys.exit(1)

rows = []
bad_files = 0

for path in paper_files:
    try:
        with open(path, "r", encoding="utf-8") as f:
            paper = json.load(f)
    except Exception as e:
        print(f"[WARN] Skipping '{path}': cannot parse JSON ({e})")
        bad_files += 1
        continue

    pid = paper.get("paper_id") or os.path.splitext(os.path.basename(path))[0]
    meta = paper.get("metadata", {}) or {}
    evall = paper.get("evidence", {}) if isinstance(paper.get("evidence"), dict) else {}
    over = (paper.get("scoring", {}) or {}).get("score_override", {}) or {}
    total_override = (paper.get("scoring", {}) or {}).get("total_score_manual_override", None)

    detail_scores: Dict[str, int] = {}
    total = 0
    lines = [f"# {pid}",
             f"Title: {meta.get('title', '')}",
             f"Link:  {meta.get('link', '')}",
             ""]

    for cfg in CRITERIA_LIST:
        cid = cfg["id"]
        weight = int(cfg["weight"])
        ev = evall.get(cid, {}) if isinstance(evall, dict) else {}
        override = over.get(cid, None)

        if override is not None:
            try:
                score = clamp(int(override), 0, weight)
            except Exception:
                score = 0
            reason = f"Manual override = {score}"
        else:
            score = compute_score(weight, ev)
            reason = "Computed score based on extended evidence"

        detail_scores[cid] = score
        total += score

        print(f"[DEBUG] Paper '{pid}', Criterion '{cid}': present={ev.get('present')}, present_confidence={ev.get('present_confidence')}, quotes_count={len(ev.get('quotes_or_pointers') or [])}, avg_quote_quality={ev.get('quote_quality')}, notes_quality={ev.get('notes_quality')}, score={score}")

        lines.append(f"## {cfg['name']}")
        lines.append(f"- Score: {score} / {weight}  ({reason})")

        quotes = _normalize_quotes(ev.get("quotes_or_pointers") if isinstance(ev, dict) else None)
        if quotes:
            lines.append("- Evidence pointers:")
            for q in quotes[:8]:
                lines.append(f"  - {q}")
        notes = (ev.get("assessor_notes") if isinstance(ev, dict) else "") or ""
        if notes:
            lines.append(f"- Notes: {notes}")
        lines.append("")

    if total_override is not None:
        try:
            total = int(total_override)
        except Exception:
            pass

    rpath = os.path.join(REPORTS_DIR, f"{pid}.md")
    try:
        with open(rpath, "w", encoding="utf-8") as rf:
            rf.write("\n".join(lines))
    except Exception as e:
        print(f"[WARN] Failed to write report '{rpath}': {e}")

    rows.append({"paper_id": pid, **detail_scores, "total_score": total})

csv_path = os.path.join(OUT_DIR, "scores.csv")
fieldnames = ["paper_id"] + [c["id"] for c in CRITERIA_LIST] + ["total_score"]
try:
    with open(csv_path, "w", newline="", encoding="utf-8") as cf:
        w = csv.DictWriter(cf, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
except Exception as e:
    print(f"[WARN] Failed to write CSV '{csv_path}': {e}")

summary_path = os.path.join(OUT_DIR, "summary.md")
rows_sorted = sorted(rows, key=lambda x: x["total_score"], reverse=True)
top5 = rows_sorted[:5]
try:
    with open(summary_path, "w", encoding="utf-8") as sf:
        sf.write("# Summary\n\n")
        sf.write(f"Total papers scored: {len(rows)}\n\n")
        if bad_files:
            sf.write(f"Skipped malformed JSON files: {bad_files}\n\n")
        sf.write("## Top-5 by total score\n")
        for i, item in enumerate(top5, 1):
            sf.write(f"{i}. {item['paper_id']} — {item['total_score']}\n")
        sf.write("\n## Notes\n- Scoring leverages presence confidence, evidence and notes quality, and evidence types.\n- Manual overrides are respected.\n")
except Exception as e:
    print(f"[WARN] Failed to write summary '{summary_path}': {e}")

print(f"Done. Wrote {csv_path}, {summary_path} and {len(rows)} reports to {REPORTS_DIR}")
if bad_files:
    print(f"[INFO] Skipped {bad_files} malformed JSON file(s).")
