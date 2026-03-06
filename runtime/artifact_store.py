from __future__ import annotations

import json
import os
from typing import Any


class ArtifactStore:
    def __init__(self, root_dir: str, session_id: str):
        self.root_dir = os.path.join(root_dir, session_id)
        os.makedirs(self.root_dir, exist_ok=True)

    def abs_path(self, relative_path: str) -> str:
        return os.path.join(self.root_dir, relative_path)

    def write_json(self, relative_path: str, payload: Any) -> str:
        path = self.abs_path(relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return relative_path

    def write_text(self, relative_path: str, content: str) -> str:
        path = self.abs_path(relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return relative_path
