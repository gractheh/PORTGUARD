"""BaseAgent — base class for all PORTGUARD compliance agents (rule-based, no external API)."""


class BaseAgent:
    AGENT_NAME: str = "BaseAgent"

    def __init__(self) -> None:
        pass

    async def _call_structured(
        self,
        user_prompt: str,
        tool_name: str,
        tool_description: str,
        output_schema: dict,
    ) -> dict:
        """Stub kept for test-mock compatibility — agents use rule-based logic, not this method."""
        raise NotImplementedError(
            f"{self.AGENT_NAME}: rule-based agents do not call _call_structured"
        )
