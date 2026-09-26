#!/usr/bin/env python3
#
# Requires `langdetect` and `fast-langdetect` (not project dependencies —
# install once with: .venv/bin/pip install langdetect fast-langdetect).
# fast-langdetect's bundled "lite" model is used, so no model download is
# needed. Architecture names flagged by fastText and not yet in the
# translation cache are translated with Gemini, so the project's .env
# credentials must work for those.
"""
Translation-to-English audit of every extraction output under outputs/gemini/.

Every stage's prompt requires its free-text fields to be translated to
English while extracting. This script checks how well that held, over every
requirement, concepts, architecture and decision JSON file the pipeline has
ever produced — requirement `description`, concept `name`, architecture unit/
pattern/connector `name` + `description`, decision `rationale`.

Method
------
Each scored text is run through `langdetect` (seeded for determinism). A text
counts as a CONFIDENT non-English leak only above both:
  MIN_CHARS   texts shorter than this are too noisy for language detection
              (a two-word product name reads as "German" as often as not) —
              they are skipped entirely, not counted as English.
  MIN_CONF    the detector's own confidence for its top-ranked language.
A text detected as non-English below MIN_CONF is a BORDERLINE case — logged
separately, never counted as a leak. Hand-checking a sample at the shipped
thresholds (20 chars, 0.85 confidence) found this catches real, mostly
full-sentence source-language text and essentially no false positives from
short technical terms.

Concept names are the exception and are scored differently. They are short
Volere category labels ("15c. Privacy Requirements"), and `langdetect` reads
many English ones as Catalan at confidence 1.0. For concepts only:
  - the Volere numbering prefix ("15c. ") is stripped first;
  - the label is classified with fastText's language-identification model
    (fast-langdetect, bundled "lite" model), which is trained to handle short
    text;
  - every name is scored — no MIN_CHARS, since short labels are the whole
    field — and a name is a leak when fastText's top language is not English.
    No confidence threshold is applied: fastText's scores on two- or
    three-word labels are low (0.3–0.4) even when its language is right.
Checked by hand over all 73 distinct concept names in outputs/gemini/: no
English label is flagged, and every flagged one is genuine Spanish/Catalan;
one genuine Catalan label ("Requisit no funcional – Rendiment") is read as
English and missed. Concepts therefore have no borderline cases.

Architecture unit/pattern/connector names are short too, and are scored like
concept names — fastText on every name, no MIN_CHARS — with one extra check,
because names also contain product names and English layer names, which
fastText misreads at any confidence ("Kubernetes" as Spanish, "Jest" as
Polish at 1.0, "Business layer" as Russian): over the final-version runs it
flagged 88 distinct names, of which only ~36 were really Spanish/Catalan. A
flagged name therefore counts as untranslated only if its faithful English
translation differs from it (case- and whitespace-insensitively): a product
name or an English name translates to itself and drops out, "Capa de negoci"
becomes "Business layer" and counts. Translations come from Gemini at
temperature 0 and are cached in TRANSLATION_CACHE, shared with
cross_lingual_similarity.py; a name already in the cache is not re-sent. A
name flagged by fastText that translates to itself is logged as borderline,
never as a leak.

Architecture is reported as two rows. Names are the matching anchor of units
and patterns; descriptions are the anchor of connectors (stored among the
units, with type "Connector") and a non-anchor field of units and patterns.
The description row is the total over all three kinds.

This script only reads outputs/gemini/ and the ground truth is never touched.
It writes the report (and adds any new name translations to the translation
cache) under analysis/report/ and nothing else — no output file, code file,
or anything under outputs/ is modified.

Scope: the final prompt of each stage only — requirement v2 (with its
concepts), architecture v3, decision v4 — i.e. those versions' design-set runs
plus every validation run (gemini-3-1 and gemini-3-5, which used the final
prompts). Files that sit directly in a document folder rather than in a run
folder are skipped: they are not runs.

Output: analysis/report/translation_audit.xlsx, one sheet:
  By Stage & Version  per stage (architecture split into name and description)
                      and version bucket: files, files with at least one
                      leak, fields scored, untranslated fields, and the
                      untranslated rate (untranslated / scored, pooled over
                      the files).

Usage (from any directory):
    analysis/script/translation_audit.py
"""
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GEMINI_ROOT = PROJECT_ROOT / "outputs" / "gemini"
REPORT_DIR = PROJECT_ROOT / "analysis" / "report"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
_REEXEC_FLAG = "TRANSLATION_AUDIT_REEXEC"


def _sysctl(name: str) -> str:
    try:
        return subprocess.run(["/usr/sbin/sysctl", "-n", name],
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def _ensure_project_interpreter() -> None:
    """Re-run the script under the project's virtualenv, natively — see the
    identical guard in analysis/script/ispartof_causes.py for why."""
    if os.environ.get(_REEXEC_FLAG) or not VENV_PYTHON.exists():
        return
    in_venv = Path(sys.prefix).resolve() == VENV_PYTHON.parent.parent.resolve()
    translated = sys.platform == "darwin" and _sysctl("sysctl.proc_translated") == "1"
    if in_venv and not translated:
        return
    command = [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
    if sys.platform == "darwin" and _sysctl("hw.optional.arm64") == "1":
        command = ["/usr/bin/arch", "-arm64", *command]
    os.environ[_REEXEC_FLAG] = "1"
    os.execv(command[0], command)


_ensure_project_interpreter()

import json  # noqa: E402

import pandas as pd  # noqa: E402

try:
    from fast_langdetect import detect as fasttext_detect
    from langdetect import DetectorFactory, detect_langs
except ImportError:
    print("Missing dependency: run '.venv/bin/pip install langdetect fast-langdetect' "
          "and re-run this script.")
    sys.exit(1)

DetectorFactory.seed = 0  # deterministic across runs

sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MIN_CHARS = 20
MIN_CONF = 0.85

TRANSLATION_CACHE = REPORT_DIR / "cross_lingual_translations.json"
TRANSLATION_BATCH = 20

_VOLERE_PREFIX = re.compile(r"^\s*\d+\s*[a-z]{0,3}\.?\s*", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _norm(text: str) -> str:
    return _clean(text).casefold()


# ---------------------------------------------------------------------------
# Faithful English translation (Gemini, cached) — also used by
# cross_lingual_similarity.py, so both scripts share one set of translations.
# ---------------------------------------------------------------------------
_TRANSLATE_SYSTEM = "You are a professional technical translator."
_TRANSLATE_PROMPT = (
    "Translate each of the following Spanish or Catalan texts into English. Translate "
    "faithfully and literally: keep the meaning, keep technical terms, product names "
    "and identifiers unchanged, and do not summarise, paraphrase, add or omit anything. "
    "Return ONLY a JSON array of strings, with exactly one translation per input, in "
    "the same order.\n\nInput:\n"
)


def _parse_json_array(raw: str) -> list:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    return json.loads(text)


def translate_all(texts: list[str]) -> dict[str, str]:
    """{source: English translation} for every text (and whatever else is
    cached), from the cache where available and from Gemini otherwise; new
    translations are written to the cache as soon as each batch returns."""
    cache = json.loads(TRANSLATION_CACHE.read_text()) if TRANSLATION_CACHE.exists() else {}
    missing = [t for t in dict.fromkeys(texts) if t not in cache]
    if not missing:
        return cache
    from infra.gemini_client import ask_gemini  # only when something must be translated

    for start in range(0, len(missing), TRANSLATION_BATCH):
        batch = missing[start:start + TRANSLATION_BATCH]
        try:
            out = _parse_json_array(ask_gemini(_TRANSLATE_PROMPT + json.dumps(batch, ensure_ascii=False),
                                               system_prompt=_TRANSLATE_SYSTEM))
            if len(out) != len(batch):
                raise ValueError(f"expected {len(batch)} translations, got {len(out)}")
        except Exception as batch_error:
            print(f"Batch translation failed ({batch_error}); translating one by one.")
            out = [_parse_json_array(ask_gemini(_TRANSLATE_PROMPT + json.dumps([t], ensure_ascii=False),
                                                system_prompt=_TRANSLATE_SYSTEM))[0] for t in batch]
        cache.update({src: str(tr).strip() for src, tr in zip(batch, out)})
        TRANSLATION_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TRANSLATION_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
        print(f"  translated {min(start + TRANSLATION_BATCH, len(missing))}/{len(missing)}")
    return cache


# ---------------------------------------------------------------------------
# Language verdicts: (language, confidence, "en" | "leak" | "borderline"), or
# None when the text is not scored at all.
# ---------------------------------------------------------------------------
def classify_prose(text: str) -> tuple[str, float, str] | None:
    """Requirement, architecture and decision fields — see the module
    docstring's Method section."""
    if len(text) < MIN_CHARS:
        return None
    try:
        top = detect_langs(text)[0]
    except Exception:
        return "?", 0.0, "en"
    if top.lang == "en":
        return "en", top.prob, "en"
    return top.lang, top.prob, "leak" if top.prob >= MIN_CONF else "borderline"


def _fasttext_top(text: str) -> tuple[str, float]:
    result = fasttext_detect(text, model="lite", k=1)
    top = result[0] if isinstance(result, list) else result
    return top["lang"], float(top["score"])


def fasttext_flags_name(text: str) -> bool:
    """fastText's top language for a short label is not English."""
    return _fasttext_top(_clean(text))[0] != "en"


def classify_concept_name(text: str) -> tuple[str, float, str] | None:
    """Concept names only — see the module docstring's Method section."""
    label = _VOLERE_PREFIX.sub("", text).strip()
    if not label:
        return None
    lang, score = _fasttext_top(label)
    return lang, score, "en" if lang == "en" else "leak"


# Filled by prepare_name_translations() before the scan: every fastText-
# flagged architecture name in scope, mapped to its English translation.
_NAME_TRANSLATIONS: dict[str, str] = {}


def classify_architecture_name(text: str) -> tuple[str, float, str] | None:
    """Architecture names only — see the module docstring's Method section."""
    if not text:
        return None
    lang, score = _fasttext_top(text)
    if lang == "en":
        return lang, score, "en"
    translation = _NAME_TRANSLATIONS.get(text)
    if translation is None or _norm(translation) == _norm(text):
        return lang, score, "borderline"
    return lang, score, "leak"


# ---------------------------------------------------------------------------
# Per-stage: which files to look at, and which fields in them are supposed to
# be English prose.
# ---------------------------------------------------------------------------
def texts_from_requirements(data) -> list[tuple[str, str, str]]:
    items = data.get("requirements", data) if isinstance(data, dict) else data
    return [("description", it.get("id"), str(it["description"]))
           for it in (items or []) if isinstance(it, dict) and it.get("description")]


def texts_from_concepts(data) -> list[tuple[str, str, str]]:
    items = data if isinstance(data, list) else (data or {}).get("concepts", [])
    return [("name", it.get("id"), str(it["name"]))
           for it in (items or []) if isinstance(it, dict) and it.get("name")]


def _architecture_field(data, field: str) -> list[tuple[str, str, str]]:
    out = []
    if not isinstance(data, dict):
        return out
    for group in ("architectural_units", "patterns", "connectors"):
        for it in data.get(group, []) or []:
            if isinstance(it, dict) and it.get(field):
                out.append((f"{group}.{field}", it.get("id"), str(it[field])))
    return out


def texts_from_architecture_names(data) -> list[tuple[str, str, str]]:
    return _architecture_field(data, "name")


def texts_from_architecture_descriptions(data) -> list[tuple[str, str, str]]:
    return _architecture_field(data, "description")


def texts_from_decisions(data) -> list[tuple[str, str, str]]:
    items = data.get("architectural_decisions", data) if isinstance(data, dict) else data
    return [("rationale", it.get("id"), str(it["rationale"]))
           for it in (items or []) if isinstance(it, dict) and it.get("rationale")]


# The final prompt version of each pipeline stage. Only its design-set runs are
# audited; the validation runs (gemini-3-1, gemini-3-5) were all made with the
# final prompts. Concepts come out of the requirement stage.
FINAL_VERSIONS = {"requirement": "v2", "architecture": "v3", "decision": "v4"}

# (report row label, pipeline stage, folder, file pattern, fields, classifier)
STAGE_CONFIG = [
    ("requirement", "requirement", GEMINI_ROOT / "requirement", "*_requirements.json",
     texts_from_requirements, classify_prose),
    ("concepts", "requirement", GEMINI_ROOT / "requirement", "*_concepts.json",
     texts_from_concepts, classify_concept_name),
    ("architecture (name)", "architecture", GEMINI_ROOT / "architecture", "*_architecture.json",
     texts_from_architecture_names, classify_architecture_name),
    ("architecture (description)", "architecture", GEMINI_ROOT / "architecture", "*_architecture.json",
     texts_from_architecture_descriptions, classify_prose),
    ("decision", "decision", GEMINI_ROOT / "decision", "*_decision.json",
     texts_from_decisions, classify_prose),
]

_DOC_RE = re.compile(r"^CF_M\d+$")
_RUN_RE = re.compile(r"^(run_\d+|first|second|third)$")
_VERSION_RE = re.compile(r"^v\d+$")


def classify_path(rel_parts: tuple[str, ...]) -> tuple[str, str, str]:
    """(version_bucket, document, run) from a file's path parts relative to
    outputs/gemini/<stage>/ — the folder-naming convention is not uniform
    across stages (see main/pipeline_stage_summary.py's own notes on this),
    so this reads structure rather than assuming one fixed template."""
    document = next((p for p in rel_parts if _DOC_RE.match(p)), "?")
    run = next((p for p in rel_parts if _RUN_RE.match(p)), "?")

    if "gemini-3-5-flash-lite" in rel_parts or "gemini-3-5" in rel_parts:
        model = "gemini-3-5"
    elif "gemini-3-1-flash-lite" in rel_parts:
        model = "gemini-3-1"
    else:
        model = "gemini-3-1" if "validation" in rel_parts else None

    if "design" in rel_parts:
        version = next((p for p in rel_parts if _VERSION_RE.match(p)), "?")
        bucket = f"design/{version}"
    elif model:
        bucket = f"validation/{model}"
    else:
        version = next((p for p in rel_parts if _VERSION_RE.match(p)), None)
        bucket = f"flat/{version}" if version else "other"
    return bucket, document, run


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------
def scoped_files(pipeline_stage: str, base: Path, pattern: str):
    """(path, rel, bucket, document, run) for the files in scope: the stage's
    final prompt (design runs) plus every validation run, and only real runs —
    a file sitting directly in a document folder is not a run (the requirement
    design/v1 and design/v2 ones are byte-identical copies of each other)."""
    final_bucket = f"design/{FINAL_VERSIONS[pipeline_stage]}"
    for path in sorted(base.rglob(pattern)):
        rel = path.relative_to(GEMINI_ROOT)
        bucket, document, run = classify_path(rel.parts)
        if (bucket == final_bucket or bucket.startswith("validation/")) and run != "?":
            yield path, rel, bucket, document, run


def prepare_name_translations() -> None:
    """Translate every in-scope architecture name fastText flags, so
    `classify_architecture_name` can tell untranslated names from product and
    English names."""
    flagged = []
    for path, *_ in scoped_files("architecture", GEMINI_ROOT / "architecture", "*_architecture.json"):
        with open(path) as f:
            names = texts_from_architecture_names(json.load(f))
        flagged += [t for t in (_clean(raw) for _, _, raw in names) if t and fasttext_flags_name(t)]
    cache = translate_all(flagged)
    _NAME_TRANSLATIONS.update({t: cache[t] for t in flagged})


def build_report() -> dict[str, pd.DataFrame]:
    by_file_rows = []
    prepare_name_translations()

    for stage, pipeline_stage, base, pattern, extractor, classify in STAGE_CONFIG:
        for path, rel, bucket, document, run in scoped_files(pipeline_stage, base, pattern):
            try:
                with open(path) as f:
                    data = json.load(f)
                scored = extractor(data)
            except Exception as read_error:
                print(f"Could not read {rel}: {read_error}")
                continue

            scored_count = 0
            flagged, borderline = [], []
            for field, item_id, raw_text in scored:
                clean = _clean(raw_text)
                verdict = classify(clean)
                if verdict is None:
                    continue
                scored_count += 1
                lang, confidence, kind = verdict
                if kind == "en":
                    continue
                row = {"field": field, "item_id": item_id, "lang": lang,
                      "confidence": round(confidence, 3), "text": clean}
                (flagged if kind == "leak" else borderline).append(row)

            rate = round(len(flagged) / scored_count, 4) if scored_count else "-"
            langs_present = ", ".join(sorted({r["lang"] for r in flagged})) if flagged else "-"
            by_file_rows.append({
                "Stage": stage,
                "Version": bucket,
                "Document": document,
                "Run": run,
                "Path": str(rel),
                "Fields scored": scored_count,
                "Untranslated (confident)": len(flagged),
                "Untranslated rate": rate,
                "Borderline (excluded from rate)": len(borderline),
                "Language(s) found": langs_present,
            })

    by_file = pd.DataFrame(by_file_rows)

    rollup_rows = []
    if not by_file.empty:
        for (stage, version), group in by_file.groupby(["Stage", "Version"], sort=True):
            total_scored = group["Fields scored"].sum()
            total_flagged = group["Untranslated (confident)"].sum()
            rollup_rows.append({
                "Stage": stage,
                "Version": version,
                "Files": len(group),
                "Files with >=1 leak": int((group["Untranslated (confident)"] > 0).sum()),
                "Fields scored": int(total_scored),
                "Untranslated (confident)": int(total_flagged),
                "Untranslated rate": round(total_flagged / total_scored, 4) if total_scored else "-",
            })

    return {
        "By File": by_file,  # not written — only used for the console summary
        "By Stage & Version": pd.DataFrame(rollup_rows),
    }


def main() -> None:
    sheets = build_report()

    output_path = REPORT_DIR / "translation_audit.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        sheets["By Stage & Version"].to_excel(writer, sheet_name="By Stage & Version", index=False)

    print(sheets["By Stage & Version"].to_string(index=False))
    # Architecture files have two rows (names, descriptions) — count paths.
    by_file = sheets["By File"]
    leaked_paths = by_file.loc[by_file["Untranslated (confident)"] > 0, "Path"].nunique()
    print(f"\n{by_file['Path'].nunique()} files scanned, {leaked_paths} with at least one leak.")
    print(f"Report saved: {output_path}")


if __name__ == "__main__":
    main()
