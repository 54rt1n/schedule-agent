import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import openai  # For error types
import json

from schedule_nlp.llm_provider import LLMProvider
from schedule_core.core.settings import Settings

# Test Factory Method (`from_settings`) and Initialization Logic via Factory
# ==========================================================================
# conftest.py sets default test values:
# LLM_BASE_URL="http://local.test/v1", LLM_MODEL="test_model", LLM_API_KEY=None

@pytest.fixture
def mock_settings_local() -> MagicMock:
    """Provides mocked Settings with a local URL and no API key."""
    settings = MagicMock(spec=Settings)
    settings.llm_api_key = None
    settings.llm_base_url = "http://mock-local-llm/v1"
    settings.llm_model = "local-test-model"
    return settings

@pytest.fixture
def mock_settings_openai_key() -> MagicMock:
    """Provides mocked Settings for default OpenAI URL with an API key."""
    settings = MagicMock(spec=Settings)
    settings.llm_api_key = "test_openai_key_123"
    settings.llm_base_url = None # Trigger default OpenAI URL
    settings.llm_model = "gpt-4o-test"
    return settings

@pytest.fixture
def mock_settings_openai_no_key() -> MagicMock:
    """Provides mocked Settings for default OpenAI URL with NO API key."""
    settings = MagicMock(spec=Settings)
    settings.llm_api_key = None
    settings.llm_base_url = None # Trigger default OpenAI URL
    settings.llm_model = "gpt-3.5-test"
    return settings

@pytest.fixture
def mock_settings_override() -> MagicMock:
    """Provides mocked Settings with all values explicitly set."""
    settings = MagicMock(spec=Settings)
    settings.llm_api_key = "override_api_key"
    settings.llm_base_url = "http://custom-override.com/api"
    settings.llm_model = "custom-model-override"
    return settings

@patch('openai.AsyncClient')
@patch('schedule_nlp.llm_provider.logger')
def test_from_settings_local(mock_logger, mock_AsyncClient, mock_settings_local):
    """Test factory uses local settings correctly."""
    # Act
    provider = LLMProvider.from_settings(mock_settings_local)

    # Assert
    assert provider.api_key is None
    assert provider.base_url == "http://mock-local-llm/v1"
    assert provider.model == "local-test-model"
    mock_logger.warning.assert_not_called() # No warning for local URL without key
    mock_logger.info.assert_any_call( # Check for creation log
        "Creating LLMProvider from settings: model='local-test-model', base_url='http://mock-local-llm/v1'"
    )
    mock_AsyncClient.assert_called_once_with(
        api_key=None,
        base_url="http://mock-local-llm/v1"
    )

@patch('openai.AsyncClient')
@patch('schedule_nlp.llm_provider.logger')
def test_from_settings_openai_with_key(mock_logger, mock_AsyncClient, mock_settings_openai_key):
    """Test factory defaults to OpenAI URL and uses provided key."""
    # Act
    provider = LLMProvider.from_settings(mock_settings_openai_key)

    # Assert
    assert provider.api_key == "test_openai_key_123"
    assert provider.base_url == "https://api.openai.com/v1" # Default applied
    assert provider.model == "gpt-4o-test"
    mock_logger.info.assert_any_call( # Check for default URL info log
        "No LLM base URL found in settings, defaulting to OpenAI API.",
        default_url="https://api.openai.com/v1"
    )
    mock_logger.warning.assert_not_called() # Has key, so no warning
    mock_logger.info.assert_any_call( # Check for creation log
        "Creating LLMProvider from settings: model='gpt-4o-test', base_url='https://api.openai.com/v1'"
    )
    mock_AsyncClient.assert_called_once_with(
        api_key="test_openai_key_123",
        base_url="https://api.openai.com/v1"
    )

@patch('openai.AsyncClient')
@patch('schedule_nlp.llm_provider.logger')
def test_from_settings_openai_without_key(mock_logger, mock_AsyncClient, mock_settings_openai_no_key):
    """Test factory defaults to OpenAI URL and logs warning if key is missing."""
    # Act
    provider = LLMProvider.from_settings(mock_settings_openai_no_key)

    # Assert
    assert provider.api_key is None
    assert provider.base_url == "https://api.openai.com/v1" # Default applied
    assert provider.model == "gpt-3.5-test"
    mock_logger.info.assert_any_call( # Check for default URL info log
        "No LLM base URL found in settings, defaulting to OpenAI API.",
        default_url="https://api.openai.com/v1"
    )
    mock_logger.warning.assert_called_with( # Check for the specific warning
        "OpenAI API key not found in settings (LLM_API_KEY)."
    )
    mock_logger.info.assert_any_call( # Check for creation log
        "Creating LLMProvider from settings: model='gpt-3.5-test', base_url='https://api.openai.com/v1'"
    )
    mock_AsyncClient.assert_called_once_with(
        api_key=None,
        base_url="https://api.openai.com/v1"
    )

@patch('openai.AsyncClient')
@patch('schedule_nlp.llm_provider.logger')
def test_from_settings_override(mock_logger, mock_AsyncClient, mock_settings_override):
    """Test factory uses explicitly overridden settings."""
    # Act
    provider = LLMProvider.from_settings(mock_settings_override)

    # Assert
    assert provider.api_key == "override_api_key"
    assert provider.base_url == "http://custom-override.com/api"
    assert provider.model == "custom-model-override"
    # No default URL log, no missing key warning expected
    mock_logger.info.assert_any_call(
         "Creating LLMProvider from settings: model='custom-model-override', base_url='http://custom-override.com/api'"
    )
    # Check other logs weren't called unexpectedly
    assert mock_logger.info.call_count == 1 # Only the creation log
    mock_logger.warning.assert_not_called()
    mock_AsyncClient.assert_called_once_with(
        api_key="override_api_key",
        base_url="http://custom-override.com/api"
    )

# Test Direct __init__ (Minimal checks as factory is preferred)
# ==============================================================

@patch('openai.AsyncClient')
def test_direct_init_basic(mock_AsyncClient):
    """Test direct initialization assigns values correctly."""
    provider = LLMProvider(
        api_key="direct_key",
        base_url="http://direct.init/v1",
        model="direct_init_model"
    )
    assert provider.api_key == "direct_key"
    assert provider.base_url == "http://direct.init/v1"
    assert provider.model == "direct_init_model"
    mock_AsyncClient.assert_called_once_with(
        api_key="direct_key",
        base_url="http://direct.init/v1"
    )

@patch('openai.AsyncClient', side_effect=Exception("Client init failed"))
def test_direct_init_client_exception(mock_AsyncClient):
    """Test RuntimeError is raised if AsyncClient initialization fails during direct init."""
    with pytest.raises(RuntimeError, match="Failed to initialize OpenAI client: Client init failed"):
        LLMProvider(api_key="key", base_url="url", model="model")

def test_direct_init_missing_args():
    """Test direct initialization raises ValueError if required args are missing/empty."""
    with pytest.raises(ValueError, match="LLMProvider requires a base_url."):
        LLMProvider(api_key="key", base_url="", model="model") # Empty base_url
    with pytest.raises(ValueError, match="LLMProvider requires a base_url."):
        LLMProvider(api_key="key", base_url=None, model="model") # None base_url (type hint allows None, but logic checks)

    with pytest.raises(ValueError, match="LLMProvider requires a model identifier."):
        LLMProvider(api_key="key", base_url="url", model="") # Empty model
    with pytest.raises(ValueError, match="LLMProvider requires a model identifier."):
        LLMProvider(api_key="key", base_url="url", model=None) # None model (type hint allows None, but logic checks)

# --- Test _generate_json Method ---
# These tests require mocking the instance's client attribute

@pytest.fixture
def provider_instance(mock_settings_local):
    """Provides a provider instance initialized with local settings for testing methods."""
    # We patch the client *during* fixture setup to avoid needing it everywhere
    with patch('openai.AsyncClient') as mock_client_constructor:
        # Configure the mock constructor to return an AsyncMock instance
        # Use spec=True instead of the potentially already mocked class
        mock_async_client = AsyncMock(spec=True)

        # Explicitly define the expected attribute structure for the mock
        # because spec=True makes it strict.
        mock_async_client.chat = MagicMock()
        mock_async_client.chat.completions = MagicMock()
        # Assign an AsyncMock here; tests will configure its return/side effect
        mock_async_client.chat.completions.create = AsyncMock()

        mock_client_constructor.return_value = mock_async_client
        provider = LLMProvider.from_settings(mock_settings_local)
        # Attach the mock client instance for later use in tests
        provider.mock_client_instance = mock_async_client
        return provider

@pytest.mark.asyncio
async def test_generate_json_success(provider_instance):
    """Test successful JSON generation and parsing."""
    # Arrange
    mock_response = MagicMock()
    mock_message = MagicMock()
    mock_message.content = json.dumps({"result": "success", "data": [1, 2]})
    mock_response.choices = [MagicMock(message=mock_message)]

    provider_instance.mock_client_instance.chat.completions.create = AsyncMock(return_value=mock_response)

    # Act
    result = await provider_instance._generate_json("system prompt", "user message")

    # Assert
    assert result == {"result": "success", "data": [1, 2], "method": "llm"} # Check method added
    provider_instance.mock_client_instance.chat.completions.create.assert_awaited_once_with(
        model="local-test-model",
        messages=[
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user message"},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

@pytest.mark.asyncio
@patch('schedule_nlp.llm_provider.logger')
async def test_generate_json_parsing_error(mock_logger, provider_instance):
    """Test handling of invalid JSON response from LLM."""
    # Arrange
    mock_response = MagicMock()
    mock_message = MagicMock()
    mock_message.content = "This is not JSON {"
    mock_response.choices = [MagicMock(message=mock_message)]
    provider_instance.mock_client_instance.chat.completions.create = AsyncMock(return_value=mock_response)

    # Act
    result = await provider_instance._generate_json("system prompt", "user message")

    # Assert
    assert result is None
    mock_logger.warning.assert_called_once()
    assert "Invalid JSON received from LLM" in mock_logger.warning.call_args[0][0]
    assert mock_logger.warning.call_args.kwargs['raw_content'] == "This is not JSON {"

@pytest.mark.asyncio
@patch('schedule_nlp.llm_provider.logger')
async def test_generate_json_api_connection_error(mock_logger, provider_instance):
    """Test handling of API connection errors."""
    # Arrange
    # Provide the required 'request' argument for APIConnectionError
    provider_instance.mock_client_instance.chat.completions.create.side_effect = openai.APIConnectionError(
        request=MagicMock() # Provide a mock request object
    )

    # Act
    result = await provider_instance._generate_json("system prompt", "user message")

    # Assert
    assert result is None
    mock_logger.exception.assert_called_with("LLM API connection error")

@pytest.mark.asyncio
@patch('schedule_nlp.llm_provider.logger')
async def test_generate_json_rate_limit_error(mock_logger, provider_instance):
    """Test handling of rate limit errors."""
    # Arrange
    provider_instance.mock_client_instance.chat.completions.create = AsyncMock(
        side_effect=openai.RateLimitError(
            "Rate limit exceeded", response=MagicMock(), body=None
        )
    )

    # Act
    result = await provider_instance._generate_json("system prompt", "user message")

    # Assert
    assert result is None
    mock_logger.exception.assert_called_with("LLM API rate limit exceeded")


@pytest.mark.asyncio
@patch('schedule_nlp.llm_provider.logger')
async def test_generate_json_authentication_error(mock_logger, provider_instance):
    """Test handling of authentication errors."""
    # Arrange
    provider_instance.mock_client_instance.chat.completions.create = AsyncMock(
        side_effect=openai.AuthenticationError(
            "Invalid API key", response=MagicMock(), body=None
        )
    )

    # Act
    result = await provider_instance._generate_json("system prompt", "user message")

    # Assert
    assert result is None
    mock_logger.exception.assert_called_with("LLM API authentication error")

@pytest.mark.asyncio
@patch('schedule_nlp.llm_provider.logger')
async def test_generate_json_api_status_error(mock_logger, provider_instance):
    """Test handling of generic API status errors."""
    # Arrange
    mock_api_response = MagicMock()
    mock_api_response.status_code = 500
    provider_instance.mock_client_instance.chat.completions.create = AsyncMock(
        side_effect=openai.APIStatusError(
            "Server error", response=mock_api_response, body=None
        )
    )

    # Act
    result = await provider_instance._generate_json("system prompt", "user message")

    # Assert
    assert result is None
    mock_logger.exception.assert_called_with(
        "LLM API status error: 500", response=mock_api_response
    )

@pytest.mark.asyncio
@patch('schedule_nlp.llm_provider.logger')
async def test_generate_json_unexpected_error(mock_logger, provider_instance):
    """Test handling of unexpected errors during API call."""
    # Arrange
    provider_instance.mock_client_instance.chat.completions.create = AsyncMock(
        side_effect=Exception("Something totally unexpected")
    )

    # Act
    result = await provider_instance._generate_json("system prompt", "user message")

    # Assert
    assert result is None
    mock_logger.exception.assert_called_with("Unexpected error during LLM API call")

@pytest.mark.asyncio
@patch('schedule_nlp.llm_provider.logger')
async def test_generate_json_no_choices(mock_logger, provider_instance):
    """Test handling response with no choices."""
    # Arrange
    mock_response = MagicMock()
    mock_response.choices = [] # Empty choices list
    provider_instance.mock_client_instance.chat.completions.create = AsyncMock(return_value=mock_response)

    # Act
    result = await provider_instance._generate_json("system prompt", "user message")

    # Assert
    assert result is None
    mock_logger.warning.assert_called_with(
        "LLM response structure missing expected parts (choices or message)."
    )

@pytest.mark.asyncio
@patch('schedule_nlp.llm_provider.logger')
async def test_generate_json_no_message(mock_logger, provider_instance):
    """Test handling response with choice but no message."""
    # Arrange
    mock_response = MagicMock()
    # Choice exists, but message attribute is None
    mock_response.choices = [MagicMock(message=None)]
    provider_instance.mock_client_instance.chat.completions.create = AsyncMock(return_value=mock_response)

    # Act
    result = await provider_instance._generate_json("system prompt", "user message")

    # Assert
    assert result is None
    mock_logger.warning.assert_called_with(
        "LLM response structure missing expected parts (choices or message)."
    )

@pytest.mark.asyncio
@patch('schedule_nlp.llm_provider.logger')
async def test_generate_json_none_content(mock_logger, provider_instance):
    """Test handling response with message but None content."""
    # Arrange
    mock_response = MagicMock()
    mock_message = MagicMock()
    mock_message.content = None # Content is explicitly None
    mock_response.choices = [MagicMock(message=mock_message)]
    provider_instance.mock_client_instance.chat.completions.create = AsyncMock(return_value=mock_response)

    # Act
    result = await provider_instance._generate_json("system prompt", "user message")

    # Assert
    assert result is None
    mock_logger.warning.assert_called_with("Received None content from LLM.") 