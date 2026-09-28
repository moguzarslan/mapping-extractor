# Mapping Generator

A chained-prompt LLM pipeline that reads software technical documents (PDF) and extracts a **requirements-to-architecture mapping** from them. For each document the pipeline produces three linked artifacts:

1. **Requirements**: functional and quality requirements, constraints, and the domain concepts they refer to.
2. **Architecture**: architectural units (layers, components, services, devices, technologies…), patterns, and the connectors between them.
3. **Architectural decisions**: the links between architectural elements and the requirements or concepts that motivated them, each backed by a rationale taken from the document.

Every output is scored against a human-annotated ground truth, so you can compare prompt versions and models by precision, recall, F1 and per-field accuracy.

The project was built for a master's thesis on building and validating a dataset of requirements-to-architecture mappings. The [`thesis/`](thesis/) folder holds the resulting data package.

---

## Table of contents

- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Running the pipeline](#running-the-pipeline)
- [Outputs](#outputs)
- [Evaluation](#evaluation)
- [Analysis scripts](#analysis-scripts)
- [The thesis data package](#the-thesis-data-package)
- [Adding a document](#adding-a-document)

---

## How it works

```
                    ┌──────────────────────────┐
  document.pdf ───► │ Stage I   Requirements   │ ──► requirements.json + concepts.json
                    └──────────────────────────┘                 │
                    ┌──────────────────────────┐                 │
  document.pdf ───► │ Stage II  Architecture   │ ──► architecture.json
                    └──────────────────────────┘                 │
                    ┌──────────────────────────┐                 ▼
  document.pdf ───► │ Stage III Decisions      │ ◄── (requirements, concepts, architecture)
                    └──────────────────────────┘ ──► decision.json

   each stage ──► evaluated against resource/groundTruths/<stage>/  ──► .xlsx reports
```

- **Stage I (requirements)** extracts requirements and concepts from the document.
- **Stage II (architecture)** extracts architectural units, patterns and connectors, together with their `isPartOf` hierarchy.
- **Stage III (decisions)** receives the document plus the outputs of Stages I and II. It returns decisions that point back to those artifacts by ID, which is what makes the output a mapping.

Each stage comes in several **versions**, which are different prompts or prompt chains. You pick the version in `.env`. The LLM is sampled, so each document runs several times (3 by default) and the evaluation reports are averaged across runs.

The LLM backend is **Google Gemini on Vertex AI**. Matching during evaluation uses Vertex AI text embeddings.

---

## Repository layout

| Path | Contents |
|---|---|
| `main/requirement/` | Stage I: entry point, runner, strategies and version catalogue |
| `main/architecture/` | Stage II: same structure |
| `main/decision/` | Stage III: same structure |
| `main/pipeline/` | Runs all three stages in sequence |
| `service/` | Evaluators (one per stage) and prompt/response handling |
| `infra/` | Gemini client and document (PDF) handling |
| `resource/prompts/` | All prompt texts (`prompts.py`) |
| `resource/docs/<DOC_ID>/` | Input documents, one folder per document (`<DOC_ID>.pdf`) |
| `resource/groundTruths/<stage>/` | Human-annotated ground-truth workbooks |
| `outputs/` | Generated extractions and evaluation reports (created on run) |
| `analysis/` | Scripts that analyse the results across runs and documents |
| `thesis/` | Final data package that accompanies the thesis |

---

## Getting started

### Prerequisites

- Python **3.13** (the version the project was developed with)
- A Google Cloud project with **Vertex AI** enabled
- The [`gcloud` CLI](https://cloud.google.com/sdk/docs/install), used for authentication

### Installation

```bash
git clone <repository-url> MappingGenerator
cd MappingGenerator
python3 -m venv .venv
source .venv/bin/activate
pip install google-genai python-dotenv pandas numpy openpyxl scipy pypdf
```

### Authentication

The client authenticates with Application Default Credentials. You don't need an API key:

```bash
gcloud auth application-default login
```

---

## Configuration

Settings are read from a `.env` file in the project root. This file is git-ignored, so create it yourself:

```dotenv
# --- Google Cloud / Gemini ---
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_GENAI_USE_VERTEXAI=true
GEMINI_MODEL=gemini-3.1-flash-lite

# --- Documents to process (comma-separated document IDs) ---
DOCUMENTS=CF_M01,CF_M05,CF_M08

# --- Stage I: requirement extraction ---
REQUIREMENT_VERSION=v2          # v1 | v2
REQUIREMENT_RUNS=3

# --- Stage II: architecture extraction ---
ARCHITECTURE_VERSION=v3         # chained | v1 | v2 | v3 | v4
ARCHITECTURE_RUNS=3

# --- Stage III: architectural decision extraction ---
DECISION_VERSION=v4             # v1 | v2 | v3 | v4
DECISION_RUNS=3
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | yes | none | GCP project that has Vertex AI enabled |
| `GOOGLE_CLOUD_LOCATION` | no | `us-central1` | Vertex AI region |
| `GEMINI_MODEL` | no | `gemini-3.1-flash-lite` | Generation model |
| `DOCUMENTS` | yes | none | Document IDs to process, matching folders in `resource/docs/` |
| `*_VERSION` | no | `v2` / `v3` / `v3` | Prompt version per stage (see below) |
| `*_RUNS` | no | `3` | Repetitions per document for that stage |
| `EVAL_EMBEDDING_MODEL` | no | `text-embedding-005` | Embedding model used for matching during evaluation |

Versions are case-insensitive, and a bare number also works (`V2`, `v2` and `2` all select `v2`).

### Available versions

**Requirements**

| Version | What it does |
|---|---|
| `v1` | Single requirement-extraction prompt, first iteration |
| `v2` | Single requirement-extraction prompt, refined (**default**) |

**Architecture**

| Version | What it does |
|---|---|
| `chained` | Four chained passes: units → patterns → connectors → `isPartOf` links |
| `v1` | Single compacted prompt |
| `v2` | Single compacted prompt, refined |
| `v3` | Compacted prompt plus a separate connector prompt (**default**) |
| `v4` | Same as `v3` with a revised compacted prompt |

**Decisions**

| Version | What it does |
|---|---|
| `v1`–`v3` | Single decision prompt, successive iterations (`v3` is the **default**) |
| `v4` | Two prompts: one extracts the decisions, a second assigns each one's requirement/concept source |

---

## Running the pipeline

Put each input document at `resource/docs/<DOC_ID>/<DOC_ID>.pdf`, add its ID to `DOCUMENTS`, and run the commands from the project root.

### Full pipeline (all three stages)

```bash
python main/pipeline/pipeline.py
```

### A single stage

```bash
python main/requirement/requirement_extractor.py
```

```bash
python main/architecture/architecture_extractor.py
```

```bash
python main/decision/decision_extractor.py
```

When one document fails (for example a missing PDF or a response that can't be parsed), the error is logged and the remaining documents still run. If a document has no ground truth, it is extracted but not evaluated.

> **Note: Stage III inputs.** The decision stage doesn't pick up whatever Stages I and II produced most recently. It reads fixed input paths that are defined at the top of [`main/decision/runner.py`](main/decision/runner.py) (`REQUIREMENT_INPUT_SUBDIR`, `ARCHITECTURE_INPUT_SUBDIR`), relative to `outputs/gemini/`. Before running Stage III, make sure those paths point at the requirement and architecture outputs you want to build on. If they don't exist, the document is skipped.

---

## Outputs

Everything lands under `outputs/`, organised by stage, version, document and run:

```
outputs/
├── gemini/                                   # raw extractions
│   └── <stage>/<version>/<DOC_ID>/run_<n>/
│       ├── <DOC_ID>_requirements.json        # Stage I
│       ├── <DOC_ID>_concepts.json            # Stage I
│       ├── <DOC_ID>_architecture.json        # Stage II
│       └── <DOC_ID>_decision.json            # Stage III
└── evaluation/                               # scores
    └── <stage>/<version>/<DOC_ID>/
        ├── run_<n>/<DOC_ID>_<stage>_eval.xlsx   # per-run report
        └── <DOC_ID>_<stage>_eval_avg.xlsx       # average over all runs
```

Different versions write to different subfolders, so switching versions never overwrites earlier results.

---

## Evaluation

Each stage has an evaluator in [`service/`](service/). It compares the LLM output with the ground-truth workbook in `resource/groundTruths/<stage>/<DOC_ID>_ground_truth_<stage>.xlsx`.

The evaluation works in two steps:

1. **Matching.** Each extracted item is paired with at most one ground-truth item using an *anchor* field, compared by embedding cosine similarity with a default threshold of **0.75**.

   | Stage | Anchor field |
   |---|---|
   | Requirements | `description` |
   | Architecture | unit/pattern `name` (exact match first, then semantic); connector `description` |
   | Decisions | `rationale` |

2. **Scoring.**
   - The anchor gets **precision, recall and F1**, which measure how much was found and how much of it was correct.
   - Every other field (type, page number, concept, `isPartOf`, referenced IDs…) gets an **accuracy** score computed over the matched pairs.

The decision stage also produces a **"found elements only"** report. It removes ground-truth decisions whose architectural elements Stage II never extracted. What remains measures Stage III on the decisions it could actually have got right, independent of errors made upstream.

The evaluators can also be run on their own. See the usage notes at the top of each `service/*_evaluator_service.py` file.

---

## Analysis scripts

[`analysis/script/`](analysis/script/) contains scripts that read existing outputs and evaluation reports and write summary workbooks to `analysis/report/`. They don't re-run any extraction.

| Script | Question it answers |
|---|---|
| `pipeline_stage_summary.py` | Average P/R/F1 for every stage, by document set (design / evaluation) and model |
| `decision_architecture_dependency.py` | How many ground-truth decisions are impossible to extract because Stage II missed every element they cite |
| `decision_reference_density.py` | How often decisions reuse the same architectural elements (LLM output vs. ground truth) |
| `ispartof_causes.py` | Root causes of `isPartOf` errors in the architecture output (`--version design/v3`) |
| `translation_audit.py` | Whether free-text fields were actually translated to English |
| `cross_lingual_similarity.py` | How much untranslated text lowered the evaluation scores |

Run them from the project root, for example:

```bash
python analysis/script/pipeline_stage_summary.py
```

`translation_audit.py` and `cross_lingual_similarity.py` need two extra packages:

```bash
pip install langdetect fast-langdetect
```

---

## The thesis data package

[`thesis/`](thesis/) contains the final artifacts of the study:

| Folder | Contents |
|---|---|
| `thesis/docs/` | The source documents used in the study |
| `thesis/foundationalDataset/` | The human-annotated requirement, architecture and decision datasets, plus concepts not covered by them |
| `thesis/pipeline/prompt/` | The prompts used by the final pipeline (PDF) |
| `thesis/pipeline/result/output/` | LLM extractions per stage, split into the **design** set (documents the prompts were iterated on) and the **validation** set (held-out documents) |
| `thesis/pipeline/result/evaluation/` | The matching evaluation reports |
| `thesis/analysis/` | Output of the analysis scripts |

---

## Adding a document

1. Save the PDF as `resource/docs/<DOC_ID>/<DOC_ID>.pdf`.
2. To have the document evaluated, add its ground truths as `resource/groundTruths/<stage>/<DOC_ID>_ground_truth_<stage>.xlsx`. This step is optional.
3. Add `<DOC_ID>` to `DOCUMENTS` in `.env`.
