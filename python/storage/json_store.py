"""JSON file storage implementation (legacy behaviour)."""
from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple

from python import config
from python import jsonio
from python.jsonio import read_details as _read_details_json
from python.jsonio import read_json, write_json_atomically
from python.storage.base import Storage


def _details_map_to_payload(details: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "version": config.VERSION,
        "updated_timestamp": jsonio.now_iso_timestamp(),
        "details": details,
    }


class JsonStorage(Storage):
    def describe(self) -> str:
        return f"fichiers JSON ({config.DATA_DIR})"

    def get_scraping_names(self) -> List[str]:
        names: List[str] = []
        data_dir = config.DATA_DIR
        if not os.path.isdir(data_dir):
            return names
        for file_name in sorted(os.listdir(data_dir)):
            if file_name.startswith("data_") and file_name.endswith(".json"):
                name = file_name[5:-5]
                names.append(name)
        return names

    def read_scraping(self, name: str) -> Optional[Dict[str, Any]]:
        path = config.data_file(name)
        data = read_json(path, None)
        return data if isinstance(data, dict) else None

    def write_scraping(self, name: str, payload: Dict[str, Any]) -> None:
        path = config.data_file(name)
        write_json_atomically(path, payload)

    def read_details(self, base_name: str) -> Dict[str, Any]:
        path = config.details_file(base_name)
        return _read_details_json(path)

    def write_details(self, base_name: str, details: Dict[str, Any]) -> None:
        path = config.details_file(base_name)
        write_json_atomically(path, _details_map_to_payload(details))

    def read_history_scrapes(self) -> List[Dict[str, Any]]:
        path = config.scrapes_file()
        data = read_json(path, None)
        if not isinstance(data, dict):
            return []
        scrapes = data.get("scrapes")
        if isinstance(scrapes, list):
            return list(scrapes)
        # Migration, kept deliberately: commit dc6038b introduced the storage
        # layer by passing this whole document ({"version", "scrapes"}) to
        # write_history_scrapes(), which nested it under its own "scrapes"
        # key. Every JSON user ran since then has
        # {"scrapes": {"version": 1, "scrapes": [...]}} on disk. Unwrapping it
        # here recovers that history instead of silently discarding it, and
        # the next write flattens the file. Remove this branch once no
        # unflattened file can be encountered.
        if isinstance(scrapes, dict):
            nested = scrapes.get("scrapes")
            if isinstance(nested, list):
                return list(nested)
        return []

    def write_history_scrapes(self, scrapes: List[Dict[str, Any]]) -> None:
        path = config.scrapes_file()
        write_json_atomically(path, {"scrapes": scrapes})

    def read_history_modifications(self) -> Dict[str, Any]:
        path = config.modifications_file()
        data = read_json(path, None)
        if isinstance(data, dict):
            return data
        return {}

    def write_history_modifications(self, payload: Dict[str, Any]) -> None:
        path = config.modifications_file()
        write_json_atomically(path, payload)

    def read_blacklist(self) -> Dict[str, Any]:
        data = read_json(config.blacklist_file(), None)
        if isinstance(data, dict):
            return data
        return {}

    def write_blacklist(self, blacklist: Dict[str, Any]) -> None:
        write_json_atomically(config.blacklist_file(), blacklist)

    def read_companies(self) -> Dict[str, Any]:
        path = config.companies_file()
        data = read_json(path, None)
        if isinstance(data, dict):
            return data
        return {}

    def write_companies(self, payload: Dict[str, Any]) -> None:
        path = config.companies_file()
        write_json_atomically(path, payload)

    def read_history_offers(self, base_name: str) -> Dict[str, Any]:
        path = config.history_file(base_name)
        data = read_json(path, None)
        if isinstance(data, dict):
            return data
        return {}

    def write_history_offers(self, base_name: str, history: Dict[str, Any]) -> None:
        path = config.history_file(base_name)
        write_json_atomically(path, history)

    def read_scrape_state(self) -> Optional[Dict[str, Any]]:
        path = config.scrape_state_file()
        data = read_json(path, None)
        if isinstance(data, dict):
            return data
        return None

    def write_scrape_state(self, payload: Dict[str, Any]) -> None:
        path = config.scrape_state_file()
        write_json_atomically(path, payload)

    def delete_scrape_state(self) -> None:
        path = config.scrape_state_file()
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def delete_scraping(self, name: str) -> List[str]:
        moved: List[str] = []
        trash = config.trash_dir()
        os.makedirs(trash, exist_ok=True)
        for src in (
            config.data_file(name),
            config.history_file(name),
            config.details_file(name),
        ):
            if not os.path.exists(src):
                continue
            file_name = os.path.basename(src)
            try:
                shutil.move(src, os.path.join(trash, file_name))
                moved.append(file_name)
            except OSError:
                continue
        return moved

    def list_data_files(self) -> List[str]:
        files: List[str] = []
        data_dir = config.DATA_DIR
        if not os.path.isdir(data_dir):
            return files
        for file_name in sorted(os.listdir(data_dir)):
            files.append(file_name)
        return files

    def list_all_offers(self, base_name: str) -> List[Tuple[str, Dict[str, Any]]]:
        payload = self.read_scraping(base_name)
        offers: List[Tuple[str, Dict[str, Any]]] = []
        if not payload:
            return offers
        raw = payload.get("offers", [])
        if not isinstance(raw, list):
            return offers
        for offer in raw:
            if not isinstance(offer, dict):
                continue
            offer_id = str(
                offer.get("number")
                or offer.get("id")
                or offer.get("idOffreEmploi")
                or offer.get("numero")
                or ""
            )
            if offer_id:
                offers.append((offer_id, offer))
        return offers

    def get_offer_details(self, base_name: str, offer_id: str) -> Optional[Dict[str, Any]]:
        details = self.read_details(base_name)
        record = details.get(offer_id)
        if isinstance(record, dict):
            return record
        return None

    def read_profile(self) -> Dict[str, Any]:
        data = read_json(config.shared_file("profil.json"), None)
        return data if isinstance(data, dict) else {}

    def write_profile(self, payload: Dict[str, Any]) -> None:
        write_json_atomically(config.shared_file("profil.json"), payload)

    def read_tracking(self, base_name: str) -> Dict[str, Dict[str, Any]]:
        """Return the follow-up, with every field present on every offer.

        The SQLite backend has one column per field, so an unset field always
        reads back as NULL. A JSON file only stores what was written, so the
        missing keys are filled in here to keep both backends returning the
        same shape.
        """
        path = config.shared_file(f"tracking_{base_name}.json")
        data = read_json(path, None)
        if not isinstance(data, dict):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for offer_id, entry in data.items():
            record = entry if isinstance(entry, dict) else {}
            out[str(offer_id)] = {
                "statut": record.get("statut"),
                "statut_date": record.get("statut_date"),
                "remarque": record.get("remarque"),
                "favori": bool(record.get("favori")),
                "priorite": record.get("priorite"),
            }
        return out

    def write_tracking(
        self, base_name: str, offer_id: str, fields: Dict[str, Any]
    ) -> None:
        """Upsert the tracking of one offer, merging like the SQLite backend.

        Only the keys present in ``fields`` are written: an absent key leaves
        the stored value untouched, while an explicit ``None`` clears it.
        """
        if not isinstance(fields, dict) or not fields:
            return
        path = config.shared_file("tracking_%s.json" % base_name)
        data = read_json(path, {})
        if not isinstance(data, dict):
            data = {}
        entry = data.get(str(offer_id))
        merged = dict(entry) if isinstance(entry, dict) else {}
        for key, value in fields.items():
            merged[key] = bool(value) if key == "favori" else value
        data[str(offer_id)] = merged
        write_json_atomically(path, data)

    def delete_tracking(self, base_name: str, offer_id: str) -> None:
        path = config.shared_file(f"tracking_{base_name}.json")
        data = read_json(path, {})
        if isinstance(data, dict) and str(offer_id) in data:
            del data[str(offer_id)]
            write_json_atomically(path, data)

    def exists(self, kind: str, **kwargs: Any) -> bool:
        if kind == "scraping":
            name = kwargs.get("name", "")
            return os.path.exists(config.data_file(name))
        if kind == "details":
            base_name = kwargs.get("base_name", "")
            return os.path.exists(config.details_file(base_name))
        return False
