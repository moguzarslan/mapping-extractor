from resource.prompts.prompts import Prompts
import os
from pathlib import Path
from dotenv import load_dotenv
from service.prompt_service import process_decision_extraction
from service.decision_evaluator_service import evaluate_decisions
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

                # The decision pass consumes the three already-final extraction
                # artifacts (requirements, concepts, architecture) plus the
                # document itself; it produces no new ids of its own for them.
                requirements_json_path = f"../outputs/gemini/{file_name}/{file_name}_requirements.json"
                concepts_json_path = f"../outputs/gemini/{file_name}/{file_name}_concepts.json"
                architecture_json_path = f"../outputs/gemini/architecture/{file_name}/{file_name}_architecture.json"

                if not Path(requirements_json_path).exists():
                    print(f"No extracted requirements found for '{file_name}' at {requirements_json_path}, skipping.")
                    continue
                if not Path(concepts_json_path).exists():
                    print(f"No extracted concepts found for '{file_name}' at {concepts_json_path}, skipping.")
                    continue
                if not Path(architecture_json_path).exists():
                    print(f"No extracted architecture found for '{file_name}' at {architecture_json_path}, skipping.")
                    continue

                decision_output_dir = "../outputs/gemini/decision/" + file_name
                llm_json_path = process_decision_extraction(
                    file=file,
                    requirements_json_dir=requirements_json_path,
                    concepts_json_dir=concepts_json_path,
                    architecture_json_dir=architecture_json_path,
                    prompt=Prompts.ARCHITECTURAL_DECISION_EXTRACTION_PROMPT,
                    output_dir=decision_output_dir,
                    output_file_name=file_name + "_decision",
                )
                print(f"Architectural decisions saved: {llm_json_path}")

                # Evaluate against the decision ground truth if available. The
                # decision evaluator additionally needs the architecture,
                # requirement and concept ground truths/LLM outputs, since a
                # decision's references (architecturalElementId,
                # architecturalDecisionSource) are only meaningful once
                # resolved through those artifacts.
                gt_decision_path = f"../resource/groundTruths/decision/{file_name}_ground_truth_decision.xlsx"
                gt_architecture_path = f"../resource/groundTruths/architecture/{file_name}_ground_truth_architecture.xlsx"
                gt_requirement_path = f"../resource/groundTruths/requirement/{file_name}_ground_truth.xlsx"
                gt_concept_path = f"../resource/groundTruths/concept/{file_name}_ground_truth_concept.xlsx"
                if (Path(gt_decision_path).exists() and Path(gt_architecture_path).exists()
                        and Path(gt_requirement_path).exists() and Path(gt_concept_path).exists()):
                    eval_output_path = f"../outputs/evaluation/decision/{file_name}_decision_eval.xlsx"
                    evaluate_decisions(
                        gt_decision_path=gt_decision_path,
                        llm_decision_path=llm_json_path,
                        gt_architecture_path=gt_architecture_path,
                        llm_architecture_path=architecture_json_path,
                        gt_requirement_path=gt_requirement_path,
                        gt_concept_path=gt_concept_path,
                        llm_requirement_path=requirements_json_path,
                        llm_concepts_path=concepts_json_path,
                        output_path=eval_output_path,
                    )
                    print(f"Decision evaluation saved: {eval_output_path}")
                else:
                    print(f"No decision ground truth found for '{file_name}', skipping evaluation.")
            except Exception as file_error:
                print(f"Error while processing '{file_name}': {file_error}")

    except Exception as e:
        print(f"Startup error: {e}")
