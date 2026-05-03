from __future__ import annotations

from .base import AgentLoader, UploadBundle
from ..schema import AgentManifest, AgentInfo, LoaderMeta


class OpenAIAssistantsLoader(AgentLoader):
    framework = "openai_assistants"

    def detect(self, bundle: UploadBundle) -> bool:
        return bool(bundle.metadata.get("assistant_id"))

    def load(self, bundle: UploadBundle) -> AgentManifest:
        warnings: list[str] = [
            "OpenAI Assistants loader — full tool schema requires a live API call at runtime; "
            "only metadata available at static analysis time"
        ]
        secrets_required: list[str] = ["OPENAI_API_KEY"]

        assistant_id: str = bundle.metadata["assistant_id"]
        agent_name: str = bundle.metadata.get("name") or assistant_id

        return AgentManifest(
            agent=AgentInfo(
                name=agent_name,
                framework="openai_assistants",
            ),
            secrets_required=secrets_required,
            loader=LoaderMeta(detected_by="openai_assistants", warnings=warnings),
        )
