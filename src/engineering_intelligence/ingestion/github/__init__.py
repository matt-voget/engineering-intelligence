"""Read-only GitHub ingestion."""

from engineering_intelligence.ingestion.github.client import GitHubClient
from engineering_intelligence.ingestion.github.service import GitHubIngestionService

__all__ = ["GitHubClient", "GitHubIngestionService"]
