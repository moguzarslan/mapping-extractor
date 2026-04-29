import base64
import mimetypes
from pathlib import Path
from typing import Union, List


class ImageTransformer:

    @classmethod
    def from_folder(cls, folder_path: Union[str, Path]) -> List[dict]:
        """
        Reads all PNG files from a folder and converts them
        into OpenAI-compatible content parts.
        """
        folder = Path(folder_path)

        if not folder.exists() or not folder.is_dir():
            raise ValueError(f"Invalid folder path: {folder}")

        image_files = sorted(folder.glob("*.png"))

        if not image_files:
            raise ValueError(f"No PNG files found in: {folder}")

        return [cls.to_content_part(img) for img in image_files]

    @staticmethod
    def to_data_url(image_path: Union[str, Path]) -> str:
        path = Path(image_path)

        if not path.exists():
            raise FileNotFoundError(f"Image file not found: {path}")

        mime_type, _ = mimetypes.guess_type(path.name)
        if not mime_type or not mime_type.startswith("image/"):
            raise ValueError(f"Unsupported image file: {path}")

        with path.open("rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")

        return f"data:{mime_type};base64,{encoded}"

    @classmethod
    def to_content_part(cls, image_path: Union[str, Path]) -> dict:
        return {
            "type": "image_url",
            "image_url": {
                "url": cls.to_data_url(image_path)
            },
        }