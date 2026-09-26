# app/services/workflows/templates/__init__.py
"""
Workflow templates. Importing each module registers its workflows.
"""
from app.services.workflows.templates import (  # noqa: F401
    finance,
    inventory,
    management,
    operations,
    procurement,
    sales,
)