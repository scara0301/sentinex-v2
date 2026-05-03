from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from ..schema import AgentManifest, FrameworkType


@dataclass
class UploadBundle:
    """Represents an uploaded agent — either a file path or a directory."""
    root: Path
    entry_file: Optional[Path] = None  # main .py or config file
    metadata: dict = field(default_factory=dict)  # any extra config (e.g. assistant_id for OpenAI)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class AgentLoader(ABC):
    framework: FrameworkType

    @abstractmethod
    def detect(self, bundle: UploadBundle) -> bool:
        """Return True if this loader can handle the bundle."""

    @abstractmethod
    def load(self, bundle: UploadBundle) -> AgentManifest:
        """Parse bundle into AgentManifest. MUST NOT import or exec user code."""
