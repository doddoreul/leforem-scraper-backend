"""Storage abstraction layer.

Supports multiple backends (JSON files or SQLite). The default
implementation keeps the existing JSON behaviour so all tests still pass.
"""
from __future__ import annotations

import abc
import os
from typing import Any, Dict, List, Optional, Tuple


class Storage(abc.ABC):
    """Abstract storage interface."""

    def describe(self) -> str:
        """Human-readable description of where the data is stored."""
        return "storage"

    @abc.abstractmethod
    def get_scraping_names(self) -> List[str]:
        ...

    @abc.abstractmethod
    def read_scraping(self, name: str) -> Optional[Dict[str, Any]]:
        ...

    @abc.abstractmethod
    def write_scraping(self, name: str, payload: Dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def delete_scraping(self, name: str) -> List[str]:
        ...

    @abc.abstractmethod
    def read_details(self, base_name: str) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    def write_details(self, base_name: str, details: Dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def read_history_scrapes(self) -> List[Dict[str, Any]]:
        ...

    @abc.abstractmethod
    def write_history_scrapes(self, scrapes: List[Dict[str, Any]]) -> None:
        ...

    @abc.abstractmethod
    def read_history_modifications(self) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    def write_history_modifications(self, payload: Dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def read_blacklist(self) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    def write_blacklist(self, blacklist: Dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def read_companies(self) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    def write_companies(self, payload: Dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def read_history_offers(self, base_name: str) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    def write_history_offers(self, base_name: str, history: Dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def read_scrape_state(self) -> Optional[Dict[str, Any]]:
        ...

    @abc.abstractmethod
    def write_scrape_state(self, payload: Dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def delete_scrape_state(self) -> None:
        ...

    @abc.abstractmethod
    def list_data_files(self) -> List[str]:
        ...

    @abc.abstractmethod
    def list_all_offers(self, base_name: str) -> List[Tuple[str, Dict[str, Any]]]:
        ...

    @abc.abstractmethod
    def get_offer_details(self, base_name: str, offer_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abc.abstractmethod
    def exists(self, kind: str, **kwargs: Any) -> bool:
        ...

    @abc.abstractmethod
    def read_profile(self) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    def write_profile(self, payload: Dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def read_tracking(self, base_name: str) -> Dict[str, Dict[str, Any]]:
        ...

    @abc.abstractmethod
    def write_tracking(
        self, base_name: str, offer_id: str, fields: Dict[str, Any]
    ) -> None:
        ...

    @abc.abstractmethod
    def delete_tracking(self, base_name: str, offer_id: str) -> None:
        ...
