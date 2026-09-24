"""List models exposed to QML. Rows are the plain dicts the core's API returns."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
    Slot,
)

Index = QModelIndex | QPersistentModelIndex


class RowModel(QAbstractListModel):
    """Rows keyed by ``key`` and kept sorted by ``sort_key`` (ascending)."""

    countChanged = Signal()

    def __init__(self, roles: tuple[str, ...], key: str, sort_key: Callable[[dict], Any],
                 parent=None) -> None:
        super().__init__(parent)
        self._roles = roles
        self._key = key
        self._sort_key = sort_key
        self._rows: list[dict] = []

    # --- QAbstractListModel ------------------------------------------------

    def roleNames(self) -> dict[int, QByteArray]:
        return {Qt.ItemDataRole.UserRole + i: QByteArray(r.encode())
                for i, r in enumerate(self._roles)}

    def rowCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        i = role - Qt.ItemDataRole.UserRole
        if not 0 <= i < len(self._roles):
            return None
        return self._rows[index.row()].get(self._roles[i])

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return len(self._rows)

    @Slot(int, result="QVariantMap")
    def get(self, row: int) -> dict:
        return dict(self._rows[row]) if 0 <= row < len(self._rows) else {}

    # --- updates -----------------------------------------------------------

    def rows(self) -> list[dict]:
        return list(self._rows)

    def reset(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = sorted((dict(r) for r in rows), key=self._sort_key)
        self.endResetModel()
        self.countChanged.emit()

    def upsert(self, row: dict) -> int:
        """Insert or update a row, keeping the sort order. Returns its new index."""
        row = dict(row)
        old = self._find(row[self._key])
        if old is None:
            new = self._position(row)
            self.beginInsertRows(QModelIndex(), new, new)
            self._rows.insert(new, row)
            self.endInsertRows()
            self.countChanged.emit()
            return new

        if self._rows[old] == row:
            return old
        rest = self._rows[:old] + self._rows[old + 1:]
        new = self._position(row, rest)
        if new != old:
            # Qt's destination is the row *before which* the item lands, in the
            # numbering from before the move.
            self.beginMoveRows(QModelIndex(), old, old, QModelIndex(), new + (new > old))
            self._rows = rest
            self._rows.insert(new, row)
            self.endMoveRows()
        else:
            self._rows[old] = row
        idx = self.index(new)
        self.dataChanged.emit(idx, idx)
        return new

    def sync(self, rows: list[dict]) -> None:
        """Make the model hold exactly ``rows`` with minimal changes, so views
        keep their current item (a reset would send a carousel back to the start)."""
        keep = {r[self._key] for r in rows}
        removed = False
        for i in reversed(range(len(self._rows))):
            if self._rows[i][self._key] not in keep:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._rows[i]
                self.endRemoveRows()
                removed = True
        if removed:
            self.countChanged.emit()
        for r in rows:
            self.upsert(r)

    def update_where(self, match: Callable[[dict], bool], **fields) -> None:
        for i, r in enumerate(self._rows):
            if match(r) and any(r.get(k) != v for k, v in fields.items()):
                r.update(fields)
                idx = self.index(i)
                self.dataChanged.emit(idx, idx)

    def _find(self, key: Any) -> int | None:
        return next((i for i, r in enumerate(self._rows) if r[self._key] == key), None)

    def _position(self, row: dict, rows: list[dict] | None = None) -> int:
        rows = self._rows if rows is None else rows
        k = self._sort_key(row)
        return next((i for i, r in enumerate(rows) if self._sort_key(r) > k), len(rows))


MESSAGE_ROLES = ("id", "ts", "direction", "peer", "text", "msgno", "state", "tries", "read")
CARD_ROLES = MESSAGE_ROLES + ("parts", "last_id", "last_ts")
STATION_ROLES = ("name", "is_object", "last_heard", "heard_direct", "path", "lat", "lon",
                 "symbol_table", "symbol", "channel",
                 "comment", "distance_km", "bearing", "speed_kmh", "course")


def message_model(parent=None) -> RowModel:
    """Newest message first."""
    return RowModel(MESSAGE_ROLES, "id", lambda m: -m["id"], parent)


def card_model(parent=None) -> RowModel:
    """Carousel cards (see grouping.py), newest part first."""
    return RowModel(CARD_ROLES, "id", lambda c: -c["last_id"], parent)


def station_model(parent=None) -> RowModel:
    """Most recently heard station first."""
    return RowModel(STATION_ROLES, "name", lambda s: -s["last_heard"], parent)
