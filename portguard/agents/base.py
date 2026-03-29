"""BaseAgent — shared async Claude tool_use client for all PORTGUARD agents."""

import json
from anthropic import AsyncAnthropic
from portguard.config import settings


class BaseAgent:
    AGENT_NAME: str = "BaseAgent"
    SYSTEM_PROMPT: str = ""

    def __init__(self) -> None:
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = settings.portguard_model
        self.max_tokens = settings.portguard_max_tokens

    async def _call_structured(
        self,
        user_prompt: str,
        tool_name: str,
        tool_description: str,
        output_schema: dict,
    ) -> dict:
        """Call Claude with tool_use to get structured JSON output.

        Forces Claude to invoke a single named tool so the response is always
        a validated, structured dictionary ready for Pydantic model construction.

        Args:
            user_prompt: The user-turn message to send.
            tool_name: The tool name to force Claude to call.
            tool_description: A clear description of what the tool captures.
            output_schema: A full JSON Schema dict for the tool input.

        Returns:
            The dict from the tool_use block input.

        Raises:
            RuntimeError: If no tool_use block is found in the response.
        """
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.SYSTEM_PROMPT,
            tools=[
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": output_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user_prompt}],
        )
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        raise RuntimeError(f"{self.AGENT_NAME}: no tool_use block in response")
