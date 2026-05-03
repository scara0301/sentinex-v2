from pathlib import Path
from typing import Optional
from .schema import AgentManifest
from .loaders import LOADERS
from .loaders.base import UploadBundle


def normalize_upload(
    root: Path,
    entry_file: Optional[Path] = None,
    metadata: Optional[dict] = None,
) -> AgentManifest:
    """
    Given an upload directory/file, detect framework and return normalized AgentManifest.
    Tries loaders in order; first detection match wins.
    RawPythonLoader always matches as fallback.
    """
    bundle = UploadBundle(root=root, entry_file=entry_file, metadata=metadata or {})
    for loader_cls in LOADERS:
        loader = loader_cls()
        if loader.detect(bundle):
            return loader.load(bundle)
    # Should never reach here since RawPythonLoader always matches
    raise ValueError(f"No loader matched bundle at {root}")
