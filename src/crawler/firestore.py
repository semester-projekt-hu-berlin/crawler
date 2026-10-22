"""Minimal Firestore client over the REST API (no gRPC).

Authentication: a service account from ``FIREBASE_SERVICE_ACCOUNT`` (the full JSON
key as a string, used in GitHub Actions) or ``GOOGLE_APPLICATION_CREDENTIALS``
(path to the key file, for local runs).

Writes go through ``documents:batchWrite`` (max 500 writes per call). Unlike
``commit`` it applies every write independently and reports a status per write,
which lets us insert comments with an ``exists: false`` precondition: comments
that are already stored (and possibly analysed) are never overwritten, so reruns
are idempotent and never reset ``analyzed``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from crawler.config import FIRESTORE_DATABASE, FIRESTORE_PROJECT
from crawler.logging_setup import log_event

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/datastore"]
MAX_BATCH = 500
# google.rpc.Code values
OK = 0
ALREADY_EXISTS = 6
FAILED_PRECONDITION = 9
NOT_FOUND = 5


# --------------------------------------------------------------------------- values


def to_value(value: Any) -> dict[str, Any]:
    """Encode a Python value as a Firestore REST ``Value``."""
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime")
        return {"timestampValue": value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")}
    if isinstance(value, bytes):
        return {"bytesValue": base64.b64encode(value).decode()}
    if isinstance(value, list | tuple):
        return {"arrayValue": {"values": [to_value(v) for v in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {k: to_value(v) for k, v in value.items()}}}
    raise TypeError(f"cannot encode {type(value)!r}")


def from_value(value: dict[str, Any]) -> Any:
    """Decode a Firestore REST ``Value``."""
    if "nullValue" in value:
        return None
    if "booleanValue" in value:
        return value["booleanValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "stringValue" in value:
        return value["stringValue"]
    if "timestampValue" in value:
        ts = value["timestampValue"].replace("Z", "+00:00")
        # Firestore may return nanosecond precision; Python supports microseconds.
        if "." in ts:
            head, rest = ts.split(".", 1)
            frac, _, tz = rest.partition("+")
            ts = f"{head}.{frac[:6].ljust(6, '0')}+{tz}"
        return datetime.fromisoformat(ts)
    if "arrayValue" in value:
        return [from_value(v) for v in value["arrayValue"].get("values", [])]
    if "mapValue" in value:
        return {k: from_value(v) for k, v in value["mapValue"].get("fields", {}).items()}
    if "bytesValue" in value:
        return base64.b64decode(value["bytesValue"])
    if "referenceValue" in value:
        return value["referenceValue"]
    if "geoPointValue" in value:
        return value["geoPointValue"]
    raise ValueError(f"unknown value {value!r}")


def encode_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {k: to_value(v) for k, v in data.items()}


def decode_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: from_value(v) for k, v in fields.items()}


# --------------------------------------------------------------------------- writes


@dataclass
class Write:
    """One write of a batch. ``tag`` is free-form metadata for the caller."""

    body: dict[str, Any]
    tag: Any = None


@dataclass
class WriteResult:
    ok: list[Write] = field(default_factory=list)
    already_exists: list[Write] = field(default_factory=list)
    failed: list[tuple[Write, dict[str, Any]]] = field(default_factory=list)


def load_credentials() -> service_account.Credentials:
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if raw:
        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path:
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    raise RuntimeError(
        "No Firestore credentials: set FIREBASE_SERVICE_ACCOUNT (JSON key) or "
        "GOOGLE_APPLICATION_CREDENTIALS (path to the key file)."
    )


class Firestore:
    def __init__(
        self,
        project: str = FIRESTORE_PROJECT,
        database: str = FIRESTORE_DATABASE,
        session: AuthorizedSession | None = None,
    ) -> None:
        self.session = session or AuthorizedSession(load_credentials())
        self.db = f"projects/{project}/databases/{database}"
        self.root = f"{self.db}/documents"
        self.base = f"https://firestore.googleapis.com/v1/{self.db}/documents"
        self._lock = threading.Lock()
        self.write_count = 0

    def doc_name(self, path: str) -> str:
        return f"{self.root}/{path}"

    # -- HTTP

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        for attempt in range(6):
            with self._lock:
                resp = self.session.request(method, url, timeout=60, **kwargs)
            if resp.status_code in (429, 500, 502, 503, 504) or (resp.status_code == 409 and "ABORTED" in resp.text):
                wait = min(2**attempt, 30)
                log_event(log, "firestore.retry", logging.WARNING, status=resp.status_code, wait=wait)
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"Firestore {method} {url} -> {resp.status_code}: {resp.text[:500]}")
            return resp.json() if resp.content else {}
        raise RuntimeError(f"Firestore {method} {url}: retries exhausted")

    # -- reads

    def get(self, path: str) -> dict[str, Any] | None:
        docs = self.batch_get([path])
        return docs.get(path)

    def batch_get(self, paths: Sequence[str]) -> dict[str, dict[str, Any] | None]:
        """Read many documents. Returns ``{path: fields or None}``."""
        out: dict[str, dict[str, Any] | None] = {}
        prefix = self.root + "/"
        for i in range(0, len(paths), 300):
            chunk = list(paths[i : i + 300])
            if not chunk:
                continue
            result = self._request(
                "POST", f"{self.base}:batchGet", json={"documents": [self.doc_name(p) for p in chunk]}
            )
            for item in result:
                if "found" in item:
                    doc = item["found"]
                    out[doc["name"].removeprefix(prefix)] = decode_fields(doc.get("fields", {}))
                elif "missing" in item:
                    out[item["missing"].removeprefix(prefix)] = None
        return out

    def run_query(
        self,
        collection: str,
        filters: list[tuple[str, str, Any]],
        order_by: list[tuple[str, str]] | None = None,
        limit: int | None = None,
        select: list[str] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Structured query on a top-level collection. Returns ``[(doc_id, fields)]``."""
        query: dict[str, Any] = {"from": [{"collectionId": collection}]}
        conds = [{"fieldFilter": {"field": {"fieldPath": f}, "op": op, "value": to_value(v)}} for f, op, v in filters]
        if len(conds) == 1:
            query["where"] = conds[0]
        elif conds:
            query["where"] = {"compositeFilter": {"op": "AND", "filters": conds}}
        if order_by:
            query["orderBy"] = [{"field": {"fieldPath": f}, "direction": d} for f, d in order_by]
        if limit:
            query["limit"] = limit
        if select:
            query["select"] = {"fields": [{"fieldPath": f} for f in select]}
        result = self._request("POST", f"{self.base}:runQuery", json={"structuredQuery": query})
        rows = []
        for item in result:
            doc = item.get("document")
            if doc:
                rows.append((doc["name"].rsplit("/", 1)[-1], decode_fields(doc.get("fields", {}))))
        return rows

    # -- writes

    def create_write(self, path: str, data: dict[str, Any], tag: Any = None) -> Write:
        """Insert only if the document does not exist yet."""
        return Write(
            {
                "update": {"name": self.doc_name(path), "fields": encode_fields(data)},
                "currentDocument": {"exists": False},
            },
            tag,
        )

    def update_write(
        self,
        path: str,
        data: dict[str, Any],
        *,
        increments: dict[str, int | float] | None = None,
        must_exist: bool = False,
        tag: Any = None,
    ) -> Write:
        """Merge ``data`` into the document (only the given fields), optionally incrementing counters."""
        body: dict[str, Any] = {
            "update": {"name": self.doc_name(path), "fields": encode_fields(data)},
            "updateMask": {"fieldPaths": list(data.keys())},
        }
        if increments:
            body["updateTransforms"] = [{"fieldPath": k, "increment": to_value(v)} for k, v in increments.items()]
        if must_exist:
            body["currentDocument"] = {"exists": True}
        return Write(body, tag)

    def batch_write(self, writes: Iterable[Write]) -> WriteResult:
        result = WriteResult()
        writes = list(writes)
        for i in range(0, len(writes), MAX_BATCH):
            chunk = writes[i : i + MAX_BATCH]
            resp = self._request("POST", f"{self.base}:batchWrite", json={"writes": [w.body for w in chunk]})
            statuses = resp.get("status") or [{}] * len(chunk)
            for w, status in zip(chunk, statuses, strict=True):
                code = status.get("code", OK)
                if code == OK:
                    result.ok.append(w)
                elif code in (ALREADY_EXISTS, FAILED_PRECONDITION) and "currentDocument" in w.body:
                    result.already_exists.append(w)
                else:
                    result.failed.append((w, status))
            with self._lock:
                self.write_count += len(chunk)
        return result
