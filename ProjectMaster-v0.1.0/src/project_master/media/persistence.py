from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from project_master.media.models import MediaAsset, MediaAssetDerivation, MediaKind
from project_master.memory.store import SQLiteStore


class MediaAssetNotFoundError(KeyError):
    pass


class SQLiteMediaCatalog:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self._initialize()

    def _initialize(self) -> None:
        with self.store.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS media_assets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('image', 'video', 'audio')),
                    source TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
                    duration_seconds REAL,
                    width INTEGER,
                    height INTEGER,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_media_assets (
                    project_id TEXT NOT NULL
                        REFERENCES projects(id) ON DELETE CASCADE,
                    asset_id TEXT NOT NULL
                        REFERENCES media_assets(id) ON DELETE CASCADE,
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, asset_id)
                );

                CREATE TABLE IF NOT EXISTS media_asset_derivations (
                    asset_id TEXT PRIMARY KEY
                        REFERENCES media_assets(id) ON DELETE CASCADE,
                    operation TEXT NOT NULL CHECK(operation = 'video_trim'),
                    source_asset_id TEXT NOT NULL
                        REFERENCES media_assets(id) ON DELETE RESTRICT,
                    start_seconds REAL NOT NULL CHECK(start_seconds >= 0),
                    end_seconds REAL NOT NULL CHECK(end_seconds > start_seconds),
                    recipe TEXT NOT NULL CHECK(recipe = 'mp4-h264-aac-v1')
                );

                CREATE INDEX IF NOT EXISTS idx_project_media_assets_project
                    ON project_media_assets(project_id, linked_at DESC);
                CREATE INDEX IF NOT EXISTS idx_media_assets_sha256
                    ON media_assets(sha256);
                CREATE INDEX IF NOT EXISTS idx_media_asset_derivations_source
                    ON media_asset_derivations(source_asset_id);
                """
            )

    def add(
        self,
        project_id: str,
        asset: MediaAsset,
        *,
        derivation: MediaAssetDerivation | None = None,
    ) -> MediaAsset:
        if asset.project_ids != (project_id,):
            raise ValueError("A new media asset must have exactly its importing project link.")
        if asset.derivation != derivation:
            raise ValueError("The persisted derivation must match the media asset.")
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO media_assets(
                    id, name, kind, source, media_type, sha256, size_bytes,
                    duration_seconds, width, height, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset.id,
                    asset.name,
                    asset.kind.value,
                    asset.source,
                    asset.media_type,
                    asset.sha256,
                    asset.size_bytes,
                    asset.duration_seconds,
                    asset.width,
                    asset.height,
                    asset.created_at.isoformat(),
                ),
            )
            conn.execute(
                """
                INSERT INTO project_media_assets(project_id, asset_id, linked_at)
                VALUES (?, ?, ?)
                """,
                (project_id, asset.id, asset.created_at.isoformat()),
            )
            if derivation is not None:
                conn.execute(
                    """
                    INSERT INTO media_asset_derivations(
                        asset_id, operation, source_asset_id,
                        start_seconds, end_seconds, recipe
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset.id,
                        derivation.operation,
                        derivation.source_asset_id,
                        derivation.start_seconds,
                        derivation.end_seconds,
                        derivation.recipe,
                    ),
                )
        return asset

    def link(self, project_id: str, asset_id: str) -> MediaAsset:
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO project_media_assets(project_id, asset_id, linked_at)
                VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
                """,
                (project_id, asset_id),
            )
        return self.get(asset_id)

    def get(self, asset_id: str) -> MediaAsset:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM media_assets WHERE id = ?",
                (asset_id,),
            ).fetchone()
            if row is None:
                raise MediaAssetNotFoundError(asset_id)
            project_rows = conn.execute(
                """
                SELECT project_id FROM project_media_assets
                WHERE asset_id = ?
                ORDER BY project_id
                """,
                (asset_id,),
            ).fetchall()
            derivation = _derivation_for(conn, asset_id)
        return _asset_from_row(
            row,
            tuple(str(item["project_id"]) for item in project_rows),
            derivation,
        )

    def get_for_project(self, project_id: str, asset_id: str) -> MediaAsset:
        with self.store.connection() as conn:
            linked = conn.execute(
                """
                SELECT 1 FROM project_media_assets
                WHERE project_id = ? AND asset_id = ?
                """,
                (project_id, asset_id),
            ).fetchone()
        if linked is None:
            raise MediaAssetNotFoundError(asset_id)
        return self.get(asset_id)

    def find_for_project(
        self,
        project_id: str,
        *,
        source: str,
        sha256: str,
    ) -> MediaAsset | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT media_assets.id
                FROM media_assets
                JOIN project_media_assets
                  ON project_media_assets.asset_id = media_assets.id
                WHERE project_media_assets.project_id = ?
                  AND media_assets.source = ?
                  AND media_assets.sha256 = ?
                ORDER BY media_assets.created_at
                LIMIT 1
                """,
                (project_id, source, sha256),
            ).fetchone()
        return self.get(str(row["id"])) if row is not None else None

    def list_for_project(self, project_id: str) -> tuple[MediaAsset, ...]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT media_assets.*
                FROM media_assets
                JOIN project_media_assets
                  ON project_media_assets.asset_id = media_assets.id
                WHERE project_media_assets.project_id = ?
                ORDER BY media_assets.created_at DESC, media_assets.id DESC
                """,
                (project_id,),
            ).fetchall()
            result: list[MediaAsset] = []
            for row in rows:
                project_rows = conn.execute(
                    """
                    SELECT project_id FROM project_media_assets
                    WHERE asset_id = ?
                    ORDER BY project_id
                    """,
                    (row["id"],),
                ).fetchall()
                result.append(
                    _asset_from_row(
                        row,
                        tuple(str(item["project_id"]) for item in project_rows),
                        _derivation_for(conn, str(row["id"])),
                    )
                )
        return tuple(result)

    def health(self) -> bool:
        with self.store.connection() as conn:
            return conn.execute("SELECT 1 FROM media_assets LIMIT 1").fetchone() is not None or (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'media_assets'"
                ).fetchone()
                is not None
            )


def _asset_from_row(
    row: Any,
    project_ids: tuple[str, ...],
    derivation: MediaAssetDerivation | None,
) -> MediaAsset:
    return MediaAsset(
        id=str(row["id"]),
        project_ids=project_ids,
        name=str(row["name"]),
        kind=MediaKind(str(row["kind"])),
        source=str(row["source"]),
        media_type=str(row["media_type"]),
        sha256=str(row["sha256"]),
        size_bytes=int(row["size_bytes"]),
        duration_seconds=(
            float(row["duration_seconds"]) if row["duration_seconds"] is not None else None
        ),
        width=int(row["width"]) if row["width"] is not None else None,
        height=int(row["height"]) if row["height"] is not None else None,
        created_at=datetime.fromisoformat(str(row["created_at"])),
        derivation=derivation,
    )


def _derivation_for(
    conn: sqlite3.Connection,
    asset_id: str,
) -> MediaAssetDerivation | None:
    row = conn.execute(
        "SELECT * FROM media_asset_derivations WHERE asset_id = ?",
        (asset_id,),
    ).fetchone()
    if row is None:
        return None
    return MediaAssetDerivation(
        operation=str(row["operation"]),
        source_asset_id=str(row["source_asset_id"]),
        start_seconds=float(row["start_seconds"]),
        end_seconds=float(row["end_seconds"]),
        recipe=str(row["recipe"]),
    )


def is_foreign_key_error(error: sqlite3.IntegrityError) -> bool:
    return "foreign key constraint failed" in str(error).casefold()
