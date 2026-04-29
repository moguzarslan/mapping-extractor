from pathlib import Path
from pypdf import PdfReader


SUPPORTED_TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json",
    ".py", ".java", ".xml", ".html"
}

SUPPORTED_PDF = {".pdf"}


def read_document(file_path: str) -> str:
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = path.suffix.lower()

    if suffix in SUPPORTED_TEXT_EXTENSIONS:
        return path.read_text(encoding="utf-8")

    elif suffix in SUPPORTED_PDF:
        return _read_pdf(path)

    else:
        raise ValueError(f"Unsupported file type: {suffix}")


def _read_pdf(path: Path) -> str:
    reader = PdfReader(str(path))

    text_parts = []

    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        except Exception as e:
            print(f"Warning: failed to read page {i}: {e}")

    if not text_parts:
        raise ValueError("No readable text found in PDF.")

    return "\n\n".join(text_parts)