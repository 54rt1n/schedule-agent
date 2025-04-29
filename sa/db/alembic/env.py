# sa/db/alembic/env.py
# Copyright (C) 2025 Martin Bukowski

"""Alembic environment configuration."""

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Import settings class and base model metadata
from sa.core.settings import Settings # Import the class
from sqlmodel import SQLModel # Assuming SQLModel is the base declarative model

# Ensure all models are imported so metadata is populated
from sa.db import models # noqa

# Instantiate settings
settings = Settings()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# Set target_metadata to the SQLModel base metadata
# Models imported above should populate this metadata
target_metadata = SQLModel.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    # Use basic_database_url directly from instantiated settings
    url = settings.basic_database_url
    if not url:
        raise ValueError("basic_database_url must be available in settings for offline mode.")

    context.configure(
        url=url, # Use basic URL
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Use basic_database_url directly from instantiated settings
    url = settings.basic_database_url
    if not url:
        raise ValueError("basic_database_url must be available in settings for online mode.")

    # Prepare engine configuration using settings
    engine_config = {
        "sqlalchemy.url": url, # Use basic URL
    }

    connectable = engine_from_config(
        engine_config,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
