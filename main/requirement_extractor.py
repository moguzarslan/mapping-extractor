from resource.prompts.prompts import Prompts
import os
from pathlib import Path
from dotenv import load_dotenv
from service.prompt_service import extract_requirements, process_chained_prompt, split_requirements, cleanup_criteria, extract_concepts, save_requirements
from service.evaluator_service import evaluate, write_average_report, write_average_type_breakdown_report
load_dotenv()

RUN_LABELS = ["first", "second", "third"]


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
        value = os.getenv("PROMPT_CHAINING").strip()

        for file_name in file_names:

            try:
                folder = ("resource/docs/" + file_name).strip()
                file = folder + "/" + file_name + ".pdf"

                # The combined GT workbook holds both a Requirements sheet and
                # an optional Concepts sheet (Concept ID -> Description), see
                # service/evaluator_service.load_ground_truth_concepts.
                gt_path = f"resource/groundTruths/requirement/{file_name}_ground_truth_requirement.xlsx"
                has_gt = Path(gt_path).exists()
                if not has_gt:
                    print(f"No ground truth found for '{file_name}', skipping evaluation.")

                reports = []
                for run_label in RUN_LABELS:
                    print(f"Run '{run_label}' for '{file_name}'")
                    requirements_output_dir = f"outputs/gemini/requirement/{file_name}/{run_label}"

                    requirements = extract_requirements(
                        file=file,
                        folder=folder,
                        prompt=Prompts.REQUIREMENT_EXTRACTION_PROMPT,
                    )

                    requirements, concepts = extract_concepts(requirements)
                    requirements_json_path, concepts_json_path = save_requirements(
                        requirements, concepts,
                        output_file_name=file_name,
                        output_dir=requirements_output_dir
                    )
                    print(f"Requirements saved: {requirements_json_path}")
                    print(f"Concepts saved: {concepts_json_path}")

                    if has_gt:
                        eval_output_path = f"outputs/evaluation/requirement/{file_name}/{run_label}/{file_name}_req_eval.xlsx"
                        report = evaluate(gt_path, requirements, eval_output_path, llm_concepts_path=concepts)
                        reports.append(report)
                        print(f"Evaluation saved: {eval_output_path}")

                if has_gt and reports:
                    avg_output_path = f"outputs/evaluation/requirement/{file_name}/{file_name}_req_eval_avg.xlsx"
                    write_average_report(reports, avg_output_path)
                    print(f"Average evaluation saved: {avg_output_path}")

                    avg_by_type_output_path = f"outputs/evaluation/requirement/{file_name}/{file_name}_req_eval_avg_by_type.xlsx"
                    write_average_type_breakdown_report(reports, avg_by_type_output_path)
                    print(f"Average by-type evaluation saved: {avg_by_type_output_path}")
            except Exception as file_error:
                print(f"Error while processing '{file_name}': {file_error}")

    except Exception as e:
        print(f"Startup error: {e}")
