"""Journal d'audit SQLite des actions exécutées par My_OS.

Schéma cf. docs/INTERFACES.md §4. Règle d'or : pour les actions destructrices,
l'entrée est écrite **avant** l'exécution (trace même en cas de crash en cours
d'action) puis mise à jour avec le résultat — voir :meth:`AuditLog.log_pending`
et :meth:`AuditLog.update_result`. Les actions auto (niveau 0) peuvent passer
par :meth:`AuditLog.log` directement (entrée complète en une fois).

Les arguments enregistrés ne contiennent **jamais** de secret en clair :
:func:`_redact` masque les valeurs des clés contenant ``password``, ``secret``,
``token``, ``api_key`` ou ``key`` (cf. SECURITY §3 menace 4).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    tool        TEXT NOT NULL,
    args        TEXT NOT NULL,
    risk_level  INTEGER NOT NULL,
    decision    TEXT NOT NULL,
    success     INTEGER,
    reversible  INTEGER
);
"""

_REDACTED_KEY_NEEDLES: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "api_key",
    "key",
)
_REDACTED = "[REDACTED]"


def _redact(args: dict) -> dict:
    """Copie de ``args`` où les valeurs des clés sensibles sont masquées."""
    safe: dict = {}
    for key, value in args.items():
        if isinstance(key, str) and any(
            needle in key.lower() for needle in _REDACTED_KEY_NEEDLES
        ):
            safe[key] = _REDACTED
        else:
            safe[key] = value
    return safe


def _utc_now_iso() -> str:
    """Horodatage ISO 8601 en UTC, précision à la seconde."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class AuditLog:
    """Journal d'audit thread-safe (verrou + connexion SQLite partagée).

    Le fichier de base est créé à l'instanciation (ainsi que ses dossiers
    parents). La connexion utilise ``check_same_thread=False`` car on garantit
    l'exclusion mutuelle via un verrou explicite.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def log(
        self,
        tool: str,
        args: dict,
        risk_level: int,
        decision: str,
        success: bool,
        reversible: bool,
    ) -> int:
        """Écrit une entrée d'audit complète. Renvoie l'``id`` inséré.

        Signature fixée par docs/INTERFACES.md §4.
        """
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO audit(timestamp, tool, args, risk_level, decision, "
                "success, reversible) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _utc_now_iso(),
                    tool,
                    json.dumps(_redact(args), ensure_ascii=False),
                    risk_level,
                    decision,
                    int(success),
                    int(reversible),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def log_pending(
        self,
        tool: str,
        args: dict,
        risk_level: int,
        decision: str,
    ) -> int:
        """Écrit une entrée d'audit **avant** exécution.

        Renvoie l'``id`` à passer à :meth:`update_result` une fois l'action
        terminée (ou si elle a échoué — l'audit doit refléter les deux cas).
        """
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO audit(timestamp, tool, args, risk_level, decision, "
                "success, reversible) VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                (
                    _utc_now_iso(),
                    tool,
                    json.dumps(_redact(args), ensure_ascii=False),
                    risk_level,
                    decision,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def update_result(self, row_id: int, success: bool, reversible: bool) -> None:
        """Met à jour le résultat (``success`` / ``reversible``) d'une entrée pending."""
        with self._lock:
            self._conn.execute(
                "UPDATE audit SET success = ?, reversible = ? WHERE id = ?",
                (int(success), int(reversible), row_id),
            )
            self._conn.commit()

    def fetch_all(self) -> list[dict]:
        """Renvoie toutes les entrées par ordre chronologique (id croissant)."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, timestamp, tool, args, risk_level, decision, "
                "success, reversible FROM audit ORDER BY id"
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "timestamp": row[1],
                "tool": row[2],
                "args": json.loads(row[3]),
                "risk_level": row[4],
                "decision": row[5],
                "success": None if row[6] is None else bool(row[6]),
                "reversible": None if row[7] is None else bool(row[7]),
            }
            for row in rows
        ]

    def close(self) -> None:
        """Ferme la connexion SQLite sous-jacente."""
        with self._lock:
            self._conn.close()
