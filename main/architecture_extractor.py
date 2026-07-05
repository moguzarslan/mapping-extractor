from resource.prompts.prompts import Prompts
import os
from pathlib import Path
from dotenv import load_dotenv
from service.prompt_service import extract_architecture_group, extract_connectors, extract_ispartof_links, apply_ispartof, save_architecture
from service.architecture_evaluator_service import evaluate_architecture
load_dotenv()


def get_document_files_from_env(env_key: str = "DOCUMENT_FILES") -> list[str]:
    value = os.getenv(env_key, "").strip()
    if not value:
        raise ValueError(f"Environment variable '{env_key}' is empty or not set.")

    files = [item.strip() for item in value.split(",") if item.strip()]
    if not files:
        raise ValueError(f"No valid file paths found in '{env_key}'.")

    return files


if __name__ == "__main__":
    try:
        file_names = get_document_files_from_env("DOCUMENTS")

        for file_name in file_names:

            try:
                folder = ("../resource/docs/" + file_name).strip()
                file = folder + "/" + file_name + ".pdf"

                # Architecture is extracted with separate prompts whose results are
                # merged in the program. Only the document text is sent to the model.
                # Units use the AU_xx id namespace and patterns use P_xx, with no
                # cross-group part-of.
                architecture_output_dir = "../outputs/gemini/architecture/" + file_name

                # Pass A — Architectural Units (Layer, Component, Service, Device,
                # Technology, Other; connectors are extracted in Pass C). Kept in
                # memory — only the final merged file is written.
                units = extract_architecture_group(
                    file, Prompts.ARCHITECTURAL_UNIT_EXTRACTION_PROMPT, "architectural_units")

                # Pass B — Patterns (Architectural Pattern, Design Pattern).
                patterns = extract_architecture_group(
                    file, Prompts.PATTERN_EXTRACTION_PROMPT, "patterns")

                # Pass C — Connectors (the communications between units), extracted
                # with the document and the already-extracted units.
                connectors = extract_connectors(file, units)

                # Pass D — cross-group isPartOf links only (unit<->pattern, either
                # direction); the within-group links (unit->unit, pattern->pattern)
                # come from the extraction passes and are unioned in. Connectors keep
                # their own endpoint isPartOf.
                links = extract_ispartof_links(file, units, patterns)
                units = apply_ispartof(units, links)
                patterns = apply_ispartof(patterns, links)

                # Merge units + connectors with the patterns and write the single
                # canonical architecture file that the evaluation consumes.
                llm_json_path = save_architecture(
                    units, connectors, patterns,
                    output_file_name=file_name + "_architecture",
                    output_dir=architecture_output_dir,
                )
                print(f"Architecture (units + connectors + patterns) saved: {llm_json_path}")

                # Evaluate against the architecture ground truth if available.
                gt_path = f"../resource/groundTruths/architecture/{file_name}_ground_truth_architecture.xlsx"
                if Path(gt_path).exists():
                    eval_output_path = f"../outputs/evaluation/architecture/{file_name}_arch_eval.xlsx"
                    evaluate_architecture(gt_path, llm_json_path, eval_output_path)
                    print(f"Architecture evaluation saved: {eval_output_path}")
                else:
                    print(f"No architecture ground truth found for '{file_name}', skipping evaluation.")
            except Exception as file_error:
                print(f"Error while processing '{file_name}': {file_error}")

    except Exception as e:
        print(f"Startup error: {e}")
