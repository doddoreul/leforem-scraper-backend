"""SQLite storage implementation."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from python import config
from python.storage.base import Storage


def _dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class SqliteStorage(Storage):
    def __init__(self, db_path: Optional[os.PathLike[str] | str] = None) -> None:
        self._explicit_path = str(db_path) if db_path is not None else None

    @property
    def db_path(self) -> str:
        if self._explicit_path is not None:
            return self._explicit_path
        return os.path.join(config.DATA_DIR, "leforem.db")

    def describe(self) -> str:
        return f"base SQLite ({self.db_path})"

    def _connect(self) -> sqlite3.Connection:
        path = self.db_path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        conn.row_factory = _dict_factory
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema(conn)
        return conn

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
                CREATE TABLE IF NOT EXISTS scrapings (
                  id INTEGER PRIMARY KEY,
                  name TEXT UNIQUE NOT NULL,
                  label TEXT,
                  scrape_timestamp TEXT,
                  occupation_guid TEXT,
                  location_guid TEXT,
                  payload_json TEXT NOT NULL,
                  updated_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS offer_details (
                  id INTEGER PRIMARY KEY,
                  base_name TEXT NOT NULL,
                  offer_id TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  updated_at TEXT DEFAULT (datetime('now')),
                  UNIQUE(base_name, offer_id)
                );
                CREATE INDEX IF NOT EXISTS idx_offer_details_base ON offer_details(base_name);

                CREATE TABLE IF NOT EXISTS history_scrapes (
                  id INTEGER PRIMARY KEY,
                  item_json TEXT NOT NULL,
                  position INTEGER
                );

                CREATE TABLE IF NOT EXISTS history_modifications (
                  id INTEGER PRIMARY KEY CHECK (id=1),
                  payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS companies (
                  id INTEGER PRIMARY KEY CHECK (id=1),
                  payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS history_offers (
                  base_name TEXT PRIMARY KEY,
                  payload_json TEXT NOT NULL,
                  updated_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS scrape_state (
                  id INTEGER PRIMARY KEY CHECK (id=1),
                  payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS blacklist (
                  id INTEGER PRIMARY KEY CHECK (id=1),
                  payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS offer_tracking (
                  id INTEGER PRIMARY KEY,
                  base_name TEXT NOT NULL,
                  offer_id TEXT NOT NULL,
                  statut TEXT,
                  statut_date TEXT,
                  remarque TEXT,
                  favori INTEGER DEFAULT 0,
                  priorite INTEGER,
                  updated_at TEXT DEFAULT (datetime('now')),
                  UNIQUE(base_name, offer_id)
                );
                CREATE INDEX IF NOT EXISTS idx_tracking_base ON offer_tracking(base_name);

                CREATE TABLE IF NOT EXISTS profile (
                  id INTEGER PRIMARY KEY CHECK (id=1),
                  payload_json TEXT NOT NULL,
                  updated_at TEXT DEFAULT (datetime('now'))
                );
                """
        )
        self._upgrade_history_offers(conn)
        conn.commit()

    def _upgrade_history_offers(self, conn: sqlite3.Connection) -> None:
        """Replace the early per-offer ``history_offers`` schema.

        The first version stored one row per offer (``offer_id``, ``state``,
        ``timestamp``); it cannot hold the full history payload the JSON
        format uses. An old database is upgraded by recreating the table,
        which is the only shape-compatible option. The per-search history is
        rebuilt on the next scraping (or by ``migrate_to_sqlite``).
        """
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(history_offers)").fetchall()
        }
        if not columns or "payload_json" in columns:
            return
        conn.execute("DROP TABLE history_offers")
        conn.execute(
            """
            CREATE TABLE history_offers (
              base_name TEXT PRIMARY KEY,
              payload_json TEXT NOT NULL,
              updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )

    def _json_dumps(self, obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _json_loads(self, s: Optional[str]) -> Any:
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None

    def get_scraping_names(self) -> List[str]:
        with self._session() as conn:
            rows = conn.execute(
                "SELECT name FROM scrapings ORDER BY name"
            ).fetchall()
            return [r["name"] for r in rows]

    def read_scraping(self, name: str) -> Optional[Dict[str, Any]]:
        with self._session() as conn:
            row = conn.execute(
                "SELECT payload_json FROM scrapings WHERE name=?",
                (name,),
            ).fetchone()
            data = self._json_loads(row["payload_json"] if row else None)
            if isinstance(data, dict):
                return data
            return None

    def write_scraping(self, name: str, payload: Dict[str, Any]) -> None:
        payload_json = self._json_dumps(payload)
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO scrapings (name, label, scrape_timestamp, occupation_guid, location_guid, payload_json)
                VALUES (:name, :label, :scrape_timestamp, :occupation_guid, :location_guid, :payload_json)
                ON CONFLICT(name) DO UPDATE SET
                  label=excluded.label,
                  scrape_timestamp=excluded.scrape_timestamp,
                  occupation_guid=excluded.occupation_guid,
                  location_guid=excluded.location_guid,
                  payload_json=excluded.payload_json,
                  updated_at=datetime('now')
                """,
                {
                    "name": name,
                    "label": payload.get("label"),
                    "scrape_timestamp": payload.get("scrape_timestamp"),
                    "occupation_guid": payload.get("occupation_guid"),
                    "location_guid": payload.get("location_guid"),
                    "payload_json": payload_json,
                },
            )
            conn.commit()

    def delete_scraping(self, name: str) -> List[str]:
        with self._session() as conn:
            conn.execute("DELETE FROM scrapings WHERE name=?", (name,))
            conn.execute("DELETE FROM offer_details WHERE base_name=?", (name,))
            conn.execute("DELETE FROM history_offers WHERE base_name=?", (name,))
            conn.commit()
        return [
            config.data_file_name(name),
            config.history_file_name(name),
            config.details_file_name(name),
        ]

    def read_details(self, base_name: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        with self._session() as conn:
            rows = conn.execute(
                "SELECT offer_id, payload_json FROM offer_details WHERE base_name=?",
                (base_name,),
            ).fetchall()
            for r in rows:
                d = self._json_loads(r["payload_json"])
                if isinstance(d, dict):
                    out[str(r["offer_id"])] = d
        return out

    def write_details(self, base_name: str, details: Dict[str, Any]) -> None:
        if not isinstance(details, dict):
            details = {}
        with self._session() as conn:
            for offer_id, payload in details.items():
                conn.execute(
                    """
                    INSERT INTO offer_details (base_name, offer_id, payload_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(base_name, offer_id) DO UPDATE SET
                      payload_json=excluded.payload_json,
                      updated_at=datetime('now')
                    """,
                    (base_name, str(offer_id), self._json_dumps(payload)),
                )
            conn.commit()

    def read_history_scrapes(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        with self._session() as conn:
            rows = conn.execute(
                "SELECT item_json FROM history_scrapes ORDER BY position ASC, id ASC"
            ).fetchall()
            for r in rows:
                d = self._json_loads(r["item_json"])
                if isinstance(d, dict):
                    out.append(d)
        return out

    def write_history_scrapes(self, scrapes: List[Dict[str, Any]]) -> None:
        with self._session() as conn:
            conn.execute("DELETE FROM history_scrapes")
            for i, item in enumerate(scrapes):
                conn.execute(
                    "INSERT INTO history_scrapes (item_json, position) VALUES (?, ?)",
                    (self._json_dumps(item), i),
                )
            conn.commit()

    def read_history_modifications(self) -> Dict[str, Any]:
        with self._session() as conn:
            row = conn.execute(
                "SELECT payload_json FROM history_modifications WHERE id=1"
            ).fetchone()
            data = self._json_loads(row["payload_json"] if row else None)
            if isinstance(data, dict):
                return data
            return {}

    def write_history_modifications(self, payload: Dict[str, Any]) -> None:
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO history_modifications (id, payload_json)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json
                """,
                (self._json_dumps(payload),),
            )
            conn.commit()

    def read_blacklist(self) -> Dict[str, Any]:
        with self._session() as conn:
            row = conn.execute("SELECT payload_json FROM blacklist WHERE id=1").fetchone()
            data = self._json_loads(row["payload_json"] if row else None)
            if isinstance(data, dict):
                return data
            return {}

    def write_blacklist(self, blacklist: Dict[str, Any]) -> None:
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO blacklist (id, payload_json)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json
                """,
                (self._json_dumps(blacklist),),
            )
            conn.commit()

    def read_companies(self) -> Dict[str, Any]:
        with self._session() as conn:
            row = conn.execute("SELECT payload_json FROM companies WHERE id=1").fetchone()
            data = self._json_loads(row["payload_json"] if row else None)
            if isinstance(data, dict):
                return data
            return {}

    def write_companies(self, payload: Dict[str, Any]) -> None:
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO companies (id, payload_json)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json
                """,
                (self._json_dumps(payload),),
            )
            conn.commit()

    def read_history_offers(self, base_name: str) -> Dict[str, Any]:
        with self._session() as conn:
            row = conn.execute(
                "SELECT payload_json FROM history_offers WHERE base_name=?",
                (base_name,),
            ).fetchone()
            data = self._json_loads(row["payload_json"] if row else None)
            if isinstance(data, dict):
                return data
            return {}

    def write_history_offers(self, base_name: str, history: Dict[str, Any]) -> None:
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO history_offers (base_name, payload_json)
                VALUES (?, ?)
                ON CONFLICT(base_name) DO UPDATE SET
                  payload_json=excluded.payload_json,
                  updated_at=datetime('now')
                """,
                (base_name, self._json_dumps(history)),
            )
            conn.commit()

    def read_scrape_state(self) -> Optional[Dict[str, Any]]:
        with self._session() as conn:
            row = conn.execute("SELECT payload_json FROM scrape_state WHERE id=1").fetchone()
            data = self._json_loads(row["payload_json"] if row else None)
            if isinstance(data, dict):
                return data
            return None

    def write_scrape_state(self, payload: Dict[str, Any]) -> None:
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO scrape_state (id, payload_json)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json
                """,
                (self._json_dumps(payload),),
            )
            conn.commit()

    def delete_scrape_state(self) -> None:
        with self._session() as conn:
            conn.execute("DELETE FROM scrape_state WHERE id=1")
            conn.commit()

    def list_data_files(self) -> List[str]:
        # Pour compatibilité avec code existant (serveur liste fichiers)
        from python import jsonio

        return jsonio._list_data_files() if hasattr(jsonio, "_list_data_files") else []

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
        with self._session() as conn:
            row = conn.execute(
                "SELECT payload_json FROM offer_details WHERE base_name=? AND offer_id=?",
                (base_name, str(offer_id)),
            ).fetchone()
            data = self._json_loads(row["payload_json"] if row else None)
            if isinstance(data, dict):
                return data
            return None

    def exists(self, kind: str, **kwargs: Any) -> bool:
        if kind == "scraping":
            name = kwargs.get("name", "")
            with self._session() as conn:
                row = conn.execute("SELECT 1 FROM scrapings WHERE name=?", (name,)).fetchone()
                return row is not None
        if kind == "details":
            base_name = kwargs.get("base_name", "")
            offer_id = kwargs.get("offer_id")
            with self._session() as conn:
                if offer_id:
                    row = conn.execute(
                        "SELECT 1 FROM offer_details WHERE base_name=? AND offer_id=?",
                        (base_name, str(offer_id)),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT 1 FROM offer_details WHERE base_name=? LIMIT 1",
                        (base_name,),
                    ).fetchone()
                return row is not None
        return False

    def read_profile(self) -> Dict[str, Any]:
        """The single candidate profile, {} when it was never saved."""
        with self._session() as conn:
            row = conn.execute("SELECT payload_json FROM profile WHERE id=1").fetchone()
            data = self._json_loads(row["payload_json"] if row else None)
            if isinstance(data, dict):
                return data
            return {}

    def write_profile(self, payload: Dict[str, Any]) -> None:
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO profile (id, payload_json)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET
                  payload_json=excluded.payload_json,
                  updated_at=datetime('now')
                """,
                (self._json_dumps(payload),),
            )
            conn.commit()

    def read_tracking(self, base_name: str) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        with self._session() as conn:
            rows = conn.execute(
                "SELECT offer_id, statut, statut_date, remarque, favori, priorite "
                "FROM offer_tracking WHERE base_name=?",
                (base_name,),
            ).fetchall()
            for r in rows:
                out[str(r["offer_id"])] = {
                    "statut": r["statut"],
                    "statut_date": r["statut_date"],
                    "remarque": r["remarque"],
                    "favori": bool(r["favori"]),
                    "priorite": r["priorite"],
                }
        return out

    # localStorage suffix -> tracking column.
    TRACKING_FIELDS = {
        "statut": "statut",
        "statut_date": "statut_date",
        "remarque": "remarque",
        "favori": "favori",
        "priorite": "priorite",
    }

    def write_tracking(
        self, base_name: str, offer_id: str, fields: Dict[str, Any]
    ) -> None:
        """Upsert the tracking of one offer.

        Only the keys present in ``fields`` are written: an absent key leaves
        the stored value untouched, while an explicit ``None`` clears it.
        """
        if not isinstance(fields, dict):
            return

        columns: List[str] = []
        values: List[Any] = []
        for key, column in self.TRACKING_FIELDS.items():
            if key not in fields:
                continue
            value = fields[key]
            if key == "favori":
                value = 1 if value else 0
            columns.append(column)
            values.append(value)

        if not columns:
            return

        insert_columns = ["base_name", "offer_id"] + columns
        insert_values = [base_name, str(offer_id)] + values
        assignments = ", ".join("%s=?" % column for column in columns)

        sql = (
            "INSERT INTO offer_tracking (%s) VALUES (%s) "
            "ON CONFLICT(base_name, offer_id) DO UPDATE SET %s, "
            "updated_at=datetime('now')"
            % (
                ", ".join(insert_columns),
                ", ".join("?" for _ in insert_columns),
                assignments,
            )
        )

        # In an upsert the placeholders are numbered across the whole
        # statement, so the SET clause re-binds the inserted values after
        # the INSERT ones.
        with self._session() as conn:
            conn.execute(sql, insert_values + values)
            conn.commit()

    def delete_tracking(self, base_name: str, offer_id: str) -> None:
        with self._session() as conn:
            conn.execute(
                "DELETE FROM offer_tracking WHERE base_name=? AND offer_id=?",
                (base_name, str(offer_id)),
            )
            conn.commit()
