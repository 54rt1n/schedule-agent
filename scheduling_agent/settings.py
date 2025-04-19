from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional

class Settings(BaseSettings):
    # Define environment variables to load
    api_keys: List[str] = [] # Default to empty list if not set

    # Configure loading from .env file
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

# Create a single instance of the settings to be imported elsewhere
settings = Settings() 