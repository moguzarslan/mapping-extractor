import os
from pathlib import Path
from dotenv import load_dotenv
from resource.prompts.prompts import Prompts
from service.prompt_service import (
    extract_architecture_compacted,
    extract_connectors,
    save_architecture,
)
from service.architecture_evaluator_service import (
    evaluate_architecture,
    write_average_architecture_report,
)
from service.evaluator_service import write_average_type_breakdown_report
load_dotenv()

DEFAULT_RUNS = 3


def get_document_files_from_env(env_key: str = "DOCUMENT_FILES") -> list[str]:
    value = os.getenv(env_key, "").strip()
    if not value:
        raise ValueError(f"Environment variable '{env_key}' is empty or not set.")

    files = [item.strip() for item in value.split(",") if item.strip()]
    if not files:
        raise ValueError(f"No valid file paths found in '{env_key}'.")

    return files


def get_run_count(env_key: str = "ARCHITECTURE_RUNS", default: int = DEFAULT_RUNS) -> int:
    """How many times to repeat the extraction for each document. The variable is
    optional — absent or blank means `default` — so the run count is configurable
    without touching the code."""
    value = os.getenv(env_key, "").strip()
    if not value:
        return default
    try:
        count = int(value)
    except ValueError:
        raise ValueError(f"'{env_key}' must be a whole number, got {value!r}.") from None
    if count < 1:
        raise ValueError(f"'{env_key}' must be at least 1, got {count}.")
    return count


if __name__ == "__main__":
    try:
        file_names = get_document_files_from_env("DOCUMENTS")
        runs = get_run_count()

        for file_name in file_names:

            try:
                folder = ("../resource/docs/" + file_name).strip()
                file = folder + "/" + file_name + ".pdf"

                gt_path = f"../resource/groundTruths/architecture/{file_name}_ground_truth_architecture.xlsx"
                has_gt = Path(gt_path).exists()
                if not has_gt:
                    print(f"No architecture ground truth found for '{file_name}', skipping evaluation.")

                # The extraction is repeated `runs` times because the model is
                # sampled, not deterministic: a single run measures one draw, and
                # the averaged report is what characterises the prompt.
                reports = []
                for run in range(1, runs + 1):
                    run_label = f"run_{run}"
                    print(f"Run {run} of {runs} for '{file_name}'")

                    # Two prompts only. Pass A —
                    # ARCHITECTURE_EXTRACTION_PROMPT_COMPACTED_V3 returns the units
                    # AND the patterns with every isPartOf link (within- and
                    # cross-group) already resolved, so no separate pattern or
                    # linking pass is needed. Units use the AU_xx id namespace and
                    # patterns use P_xx.
                    units, patterns = extract_architecture_compacted(
                        file, Prompts.ARCHITECTURE_EXTRACTION_PROMPT_COMPACTED_V3)

                    # Pass B — Connectors (the communications between units) and
                    # the Technologies those communications use, extracted with the
                    # document and the units from Pass A. `save_architecture` merges
                    # them into the units, folding each technology Pass A already
                    # found into that existing unit instead of repeating it.
                    connectors = extract_connectors(file, units)

                    # Write the same canonical architecture file the evaluation
                    # consumes.
                    llm_json_path = save_architecture(
                        units, connectors, patterns,
                        output_file_name=file_name + "_architecture",
                        output_dir=f"../outputs/gemini/architecture_v3/{file_name}/{run_label}",
                    )
                    print(f"Architecture (V3 + connector prompt) saved: {llm_json_path}")

                    if has_gt:
                        eval_output_path = (f"../outputs/evaluation/architecture_v3/"
                                            f"{file_name}/{run_label}/{file_name}_arch_eval.xlsx")
                        reports.append(evaluate_architecture(gt_path, llm_json_path, eval_output_path))
                        print(f"Architecture evaluation saved: {eval_output_path}")

                # The average over the runs. With a single run there is nothing to
                # average, so that run's own report is left to speak for itself.
                if len(reports) > 1:
                    avg_dir = f"../outputs/evaluation/architecture_v3/{file_name}"

                    avg_output_path = f"{avg_dir}/{file_name}_arch_eval_avg.xlsx"
                    write_average_architecture_report(reports, avg_output_path)
                    print(f"Average evaluation saved: {avg_output_path}")

                    avg_by_type_output_path = f"{avg_dir}/{file_name}_arch_eval_avg_by_type.xlsx"
                    write_average_type_breakdown_report(reports, avg_by_type_output_path)
                    print(f"Average by-type evaluation saved: {avg_by_type_output_path}")
            except Exception as file_error:
                print(f"Error while processing '{file_name}': {file_error}")

    except Exception as e:
        print(f"Startup error: {e}")
