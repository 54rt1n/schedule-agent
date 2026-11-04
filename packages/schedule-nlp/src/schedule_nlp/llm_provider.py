# packages/schedule-nlp/src/schedule_nlp/llm_provider.py
# Copyright (C) 2025 Martin Bukowski

"""Provides an interface to interact with Language Models (LLMs)."""

import json
import openai
from typing import Any

from schedule_core.core.logging_config import get_logger
from schedule_core.core.settings import Settings

logger = get_logger(__name__)


class LLMProvider:
    """
    Generic LLM provider that works with any OpenAI-compatible API.
    """

    def __init__(self, *, api_key: str | None, base_url: str, model: str):
        """
        Initialize the LLM provider with explicit configuration.

        Note: It's recommended to use the `from_settings` classmethod
              for standard initialization based on application settings.

        Args:
            api_key: The API key to use.
            base_url: The base URL of the OpenAI-compatible API.
            model: The specific model identifier to use.
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

        # Basic validation
        if not self.base_url:
             raise ValueError("LLMProvider requires a base_url.")
        if not self.model:
             raise ValueError("LLMProvider requires a model identifier.")

        try:
            # Initialize the underlying client
            self.client = openai.AsyncClient(api_key=self.api_key, base_url=self.base_url)
            logger.debug(f"OpenAI client configured for model '{self.model}' at {self.base_url}")
        except Exception as e:
            logger.exception(f"Failed to initialize OpenAI client for {self.base_url}")
            # Wrap the original exception
            raise RuntimeError(f"Failed to initialize OpenAI client: {e}") from e

    @classmethod
    def from_settings(cls, settings: Settings) -> 'LLMProvider':
        """
        Factory method to create an LLMProvider instance from a Settings object.

        Args:
            settings: A configured Settings object.

        Returns:
            An initialized LLMProvider instance.
        """
        api_key = settings.llm_api_key
        base_url = settings.llm_base_url
        model = settings.llm_model # Settings class already provides default "gpt-4o-mini"

        # Apply default base_url if not provided in settings
        if not base_url:
            base_url = "https://api.openai.com/v1"
            logger.info("No LLM base URL found in settings, defaulting to OpenAI API.", default_url=base_url)

        # Log warning if using default OpenAI URL without an API key
        if not api_key and "openai.com" in base_url:
            logger.warning("OpenAI API key not found in settings (LLM_API_KEY).")

        # The Settings class ensures model is a string, using its default if necessary.
        # No need for a separate fallback here.

        logger.info(f"Creating LLMProvider from settings: model='{model}', base_url='{base_url}'")
        # Call the __init__ with the resolved values
        return cls(api_key=api_key, base_url=base_url, model=model)


    async def _generate_json(
        self, system_prompt: str, user_message: str
    ) -> dict[str, Any] | list[Any] | None:
        """Internal helper to call the LLM and parse its JSON response.
        Returns None if any error occurs.
        """
        logger.debug("Generating JSON from LLM", model=self.model)
        if not self.client:
             # This should not happen if __init__ succeeded, but defensive check
             logger.error("LLM client not initialized before calling _generate_json")
             return None
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0,  # For deterministic JSON generation
                response_format={"type": "json_object"},  # Request JSON output
            )

            if not response.choices or not response.choices[0].message:
                logger.warning(
                    "LLM response structure missing expected parts (choices or message)."
                )
                return None

            content = response.choices[0].message.content
            if content is None:
                logger.warning("Received None content from LLM.")
                return None

            try:
                parsed_json = json.loads(content)
                logger.debug("Successfully parsed JSON from LLM.")
                # Add method marker if it's a dictionary and missing
                if isinstance(parsed_json, dict) and "method" not in parsed_json:
                    parsed_json["method"] = "llm"
                return parsed_json
            except json.JSONDecodeError as json_err:
                logger.warning(f"Invalid JSON received from LLM: {json_err}", raw_content=content)
                return None  # Failed to parse

        except openai.APIConnectionError:
            logger.exception("LLM API connection error")
            return None
        except openai.RateLimitError:
            logger.exception("LLM API rate limit exceeded")
            # Consider adding retry logic here with backoff
            return None
        except openai.AuthenticationError:  # Catch specific auth error
            logger.exception("LLM API authentication error")
            return None
        except openai.APIStatusError as e:
            logger.exception(f"LLM API status error: {e.status_code}", response=e.response)
            return None
        except Exception:
            logger.exception("Unexpected error during LLM API call")
            return None
