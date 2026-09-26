# app/services/workflows/__init__.py
"""
Workflow package.

Importing this package imports the templates, which register themselves
with the workflow registry. That side-effect import is intentional: it
means "importing app.services.workflows" is enough to make the registry
usable.
"""
from app.services.workflows import templates  # noqa: F401