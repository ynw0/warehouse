"""WSGI entry point used by gunicorn and packaging-based deployments."""

from app import app

__all__ = ["app"]

