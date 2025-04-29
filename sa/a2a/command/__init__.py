# sa/a2a/command/__init__.py
# Copyright (C) 2025 Martin Bukowski

from .nlp_request import handle_nlp_request
from .structured_create import handle_structured_create
from .structured_update import handle_structured_update
from .structured_query import handle_structured_query
from .structured_delete import handle_structured_delete
from .structured_export import handle_structured_export
# Add contact handlers
from .structured_contact_create import handle_structured_create_contact
from .structured_contact_query import handle_structured_query_contact
from .structured_contact_update import handle_structured_update_contact
from .structured_contact_delete import handle_structured_delete_contact

__all__ = [
    "handle_nlp_request",
    "handle_structured_create",
    "handle_structured_update",
    "handle_structured_query",
    "handle_structured_delete",
    "handle_structured_export",
    # Add contact handlers to __all__
    "handle_structured_create_contact",
    "handle_structured_query_contact",
    "handle_structured_update_contact",
    "handle_structured_delete_contact",
]
