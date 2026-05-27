import mimetypes
from pathlib import Path
from typing import Union, List
from google.genai import types


class ImageTransformer:

    @classmethod
    def from_folder(cls, folder_path: Union[str, Path]) -> List[types.Part]:
        folder = Path(folder_path)

        if not folder.exists() or not folder.is_dir():
            raise ValueError(f"Invalid folder path: {folder}")

        image_files = sorted(folder.glob("*.png"))

        if not image_files:
            raise ValueError(f"No PNG files found in: {folder}")

        return [cls.to_content_part(img) for img in image_files]

    @classmethod
    def to_content_part(cls, image_path: Union[str, Path]) -> types.Part:
        path = Path(image_path)

        if not path.exists():
            raise FileNotFoundError(f"Image file not found: {path}")

        mime_type, _ = mimetypes.guess_type(path.name)

        if not mime_type or not mime_type.startswith("image/"):
            raise ValueError(f"Unsupported image file: {path}")

        with path.open("rb") as f:
            image_bytes = f.read()

        return types.Part.from_bytes(
            data=image_bytes,
            mime_type=mime_type,
        )