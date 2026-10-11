"""报表导出服务、仓库语句和对象存储回退。不连接数据库。"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from botocore.exceptions import ClientError
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ParamInvalidError, ReportExportInvalidError
from app.engines.report import EXPORT_EXPIRED, EXPORT_FAILED, EXPORT_PENDING, EXPORT_SUCCEEDED
from app.models.finance import SkuProfitDaily
from app.models.report import ReportExport
from app.object_store import (
    DirectoryObjectStore,
    MemoryObjectStore,
    ResilientExportStore,
    S3ObjectStore,
    default_object_store,
    export_object_store,
)
from app.repositories.report import ReportExportRepository, SkuProfitExportRepository, _integrity_kind
from app.schemas.report import ReportExportCreate
from app.services.report import ReportService


class _Result:
    def __init__(self, value: object = None, rows: list[object] | None = None) -> None:
        self.value = value
        self.rows = [] if rows is None else rows

    def scalar_one_or_none(self) -> object:
        return self.value

    def scalar_one(self) -> object:
        return self.value

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[object]:
        return self.rows


class _Session:
    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.statements: list[object] = []

    def begin_nested(self) -> _Session:
        return self

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        del exc_type, exc, traceback
        return False

    def add(self, row: object) -> None:
        del row

    async def flush(self) -> None:
        if self.fail is not None:
            raise IntegrityError("INSERT", {}, Exception(self.fail))

    def expunge(self, row: object) -> None:
        del row

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        if "count(" in str(statement).lower():
            return _Result(0, [])
        return _Result(None, [])


class _Exports:
    def __init__(self) -> None:
        self.rows: dict[int, ReportExport] = {}
        self.by_key: dict[str, ReportExport] = {}
        self.active = False
        self.kind: str | None = None
        self.after_insert: ReportExport | None = None
        self.lookups = 0
        self.session = _Session()

    async def get_by_key(self, key: str) -> ReportExport | None:
        self.lookups += 1
        found = self.by_key.get(key)
        if found is not None:
            return found
        raced = self.after_insert
        if raced is not None and self.lookups > 1 and raced.idempotency_key == key:
            return raced
        return None

    async def has_active(self) -> bool:
        return self.active

    async def insert(self, row: ReportExport) -> str | None:
        if self.kind == "idempotency" and row.idempotency_key not in self.by_key:
            return "idempotency"
        if self.kind == "active":
            return "active"
        row.id = 88001
        self.rows[row.id] = row
        if row.idempotency_key is not None:
            self.by_key[row.idempotency_key] = row
        return None

    async def get(self, export_id: int) -> ReportExport | None:
        return self.rows.get(export_id)


class _Profits:
    def __init__(self, rows: list[SkuProfitDaily]) -> None:
        self.rows = rows

    async def count_between(self, date_from: date, date_to: date) -> int:
        return len([row for row in self.rows if date_from <= row.stat_date <= date_to])

    async def page_between(
        self,
        date_from: date,
        date_to: date,
        *,
        after: tuple[date, int] | None,
        limit: int,
    ) -> list[SkuProfitDaily]:
        chosen = [row for row in self.rows if date_from <= row.stat_date <= date_to]
        chosen.sort(key=lambda row: (row.stat_date, int(row.id)))
        if after is not None:
            chosen = [row for row in chosen if (row.stat_date, int(row.id)) > after]
        return chosen[:limit]


class _Audit:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    async def append_action(self, **kwargs: object) -> None:
        self.rows.append(kwargs)


def _profit(row_id: int, day: date) -> SkuProfitDaily:
    return SkuProfitDaily(
        id=row_id,
        tenant_id=1,
        stat_date=day,
        sku_id=10 + row_id,
        shop_id=20,
        currency="USD",
        book_currency="CNY",
        revenue=Decimal("1.500000"),
        quantity=2,
        lines=[],
        complete=True,
        cost_total=Decimal("0.400000"),
        net_profit=Decimal("1.100000"),
        net_margin=Decimal("0.733333"),
        book_revenue=Decimal("10.000000"),
        book_cost_total=Decimal("3.000000"),
        book_net_profit=Decimal("7.000000"),
        fx_gain=Decimal("0.010000"),
    )


def _service(
    store: MemoryObjectStore,
    profits: list[SkuProfitDaily] | None = None,
) -> tuple[ReportService, _Exports, _Audit]:
    service = ReportService(session=_Session(), store=store)  # type: ignore[arg-type]
    exports = _Exports()
    audit = _Audit()
    service.exports = exports  # type: ignore[assignment]
    service.profits = _Profits(profits or [])  # type: ignore[assignment]
    service.audit = audit  # type: ignore[assignment]
    return service, exports, audit


def _payload(file_format: str = "csv") -> ReportExportCreate:
    return ReportExportCreate(date_from=date(2026, 10, 1), date_to=date(2026, 10, 2), file_format=file_format)


async def test_create_replays_the_same_key_and_rejects_a_second_active_job() -> None:
    service, exports, audit = _service(MemoryObjectStore())
    view, created = await service.create(
        _payload(),
        tenant_id=1,
        actor_id=7,
        include_cost=True,
        idempotency_key=" export-1 ",
    )
    assert created is True
    assert view.status == EXPORT_PENDING
    assert view.include_cost is True
    assert audit.rows[0]["after"]["status"] == EXPORT_PENDING  # type: ignore[index]

    again, created_again = await service.create(
        _payload(),
        tenant_id=1,
        actor_id=7,
        include_cost=False,
        idempotency_key="export-1",
    )
    assert created_again is False
    assert again.id == view.id

    exports.active = True
    with pytest.raises(ConflictError):
        await service.create(
            _payload(),
            tenant_id=1,
            actor_id=7,
            include_cost=True,
            idempotency_key="export-2",
        )


async def test_create_rejects_bad_input_and_maps_unique_conflicts() -> None:
    service, exports, _audit = _service(MemoryObjectStore())
    with pytest.raises(ParamInvalidError):
        await service.create(_payload(), tenant_id=1, actor_id=7, include_cost=True, idempotency_key=None)
    with pytest.raises(ParamInvalidError):
        await service.create(_payload(), tenant_id=1, actor_id=7, include_cost=True, idempotency_key="k" * 129)
    with pytest.raises(ReportExportInvalidError):
        await service.create(
            ReportExportCreate(dataset="orders", date_from=date(2026, 10, 1), date_to=date(2026, 10, 1)),
            tenant_id=1,
            actor_id=7,
            include_cost=True,
            idempotency_key="bad-dataset",
        )

    exports.kind = "active"
    with pytest.raises(ConflictError, match="进行"):
        await service.create(_payload(), tenant_id=1, actor_id=7, include_cost=True, idempotency_key="race-active")

    exports.kind = "idempotency"
    with pytest.raises(ConflictError, match="冲突"):
        await service.create(_payload(), tenant_id=1, actor_id=7, include_cost=True, idempotency_key="race-key")

    raced = ReportExport(
        id=42,
        tenant_id=1,
        dataset="sku_profit",
        file_format="csv",
        status=EXPORT_PENDING,
        date_from=date(2026, 10, 1),
        date_to=date(2026, 10, 2),
        include_cost=False,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        idempotency_key="race-seen",
    )
    exports.kind = "idempotency"
    exports.after_insert = raced
    exports.lookups = 0
    view, created = await service.create(
        _payload(),
        tenant_id=1,
        actor_id=7,
        include_cost=True,
        idempotency_key="race-seen",
    )
    assert created is False
    assert view.id == "42"


async def test_execute_streams_pages_and_download_checks_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "report_export_page_size", 1)
    store = MemoryObjectStore()
    rows = [_profit(1, date(2026, 10, 1)), _profit(2, date(2026, 10, 2))]
    service, _exports, audit = _service(store, rows)
    created, _flag = await service.create(
        _payload("xlsx"),
        tenant_id=9,
        actor_id=3,
        include_cost=False,
        idempotency_key="page",
    )
    done = await service.execute(int(created.id))
    assert done.status == EXPORT_SUCCEEDED
    assert done.row_count == 2
    assert done.include_cost is False
    name, media, body = await service.file(int(created.id))
    assert name.endswith(".xlsx")
    assert "spreadsheetml" in media
    with ZipFile(BytesIO(body)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml")
    assert b"1.500000" in sheet
    assert b"0.400000" not in sheet
    assert any(item["after"]["status"] == EXPORT_SUCCEEDED for item in audit.rows)  # type: ignore[index]

    again = await service.execute(int(created.id))
    assert again.status == EXPORT_SUCCEEDED

    row = _exports_row(service)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ReportExportInvalidError, match="过期"):
        await service.file(int(created.id))


async def test_execute_fails_when_the_row_cap_or_the_writer_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryObjectStore()
    service, _exports, audit = _service(store, [_profit(1, date(2026, 10, 1))])
    created, _flag = await service.create(
        _payload(),
        tenant_id=1,
        actor_id=3,
        include_cost=True,
        idempotency_key="cap",
    )
    monkeypatch.setattr(settings, "report_export_max_rows", 0)
    failed = await service.execute(int(created.id))
    assert failed.status == EXPORT_FAILED
    assert failed.error is not None
    assert "收窄日期" in failed.error

    service.exports.rows[int(created.id)].status = EXPORT_PENDING  # type: ignore[attr-defined]
    monkeypatch.setattr(settings, "report_export_max_rows", 50_000)

    async def _boom(*_args: object, **_kwargs: object) -> list[SkuProfitDaily]:
        raise RuntimeError("disk")

    service.profits.page_between = _boom  # type: ignore[method-assign]
    broken = await service.execute(int(created.id))
    assert broken.status == EXPORT_FAILED
    assert broken.error == "导出失败"
    assert audit.rows[-1]["after"]["status"] == EXPORT_FAILED  # type: ignore[index]


async def test_get_file_and_expire_follow_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "report_export_page_size", 10)
    store = MemoryObjectStore()
    service, exports, _audit = _service(store, [_profit(1, date(2026, 10, 1))])
    with pytest.raises(NotFoundError):
        await service.get(1)
    created, _flag = await service.create(
        _payload(),
        tenant_id=4,
        actor_id=8,
        include_cost=True,
        idempotency_key="life",
    )
    found = await service.get(int(created.id))
    assert found.status == EXPORT_PENDING
    with pytest.raises(ReportExportInvalidError, match="还不能下载"):
        await service.file(int(created.id))

    await service.execute(int(created.id))
    _name, _media, body = await service.file(int(created.id))
    assert "0.400000" in body.decode("utf-8-sig")

    row = exports.rows[int(created.id)]
    row.object_key = "missing.csv"
    with pytest.raises(ReportExportInvalidError, match="过期"):
        await service.file(int(created.id))

    await service.expire(int(created.id))
    assert exports.rows[int(created.id)].status == EXPORT_EXPIRED
    await service.expire(int(created.id))
    await service.expire(999)


def _exports_row(service: ReportService) -> ReportExport:
    exports: _Exports = service.exports  # type: ignore[assignment]
    return next(iter(exports.rows.values()))


def test_repository_compiles_keyset_sql_and_names_unique_conflicts() -> None:
    session = _Session()
    exports = ReportExportRepository(session)  # type: ignore[arg-type]
    profits = SkuProfitExportRepository(session)  # type: ignore[arg-type]

    async def _read() -> None:
        assert await exports.get_by_key("k") is None
        assert await exports.has_active() is False
        assert await profits.count_between(date(2026, 10, 1), date(2026, 10, 2)) == 0
        assert await profits.page_between(date(2026, 10, 1), date(2026, 10, 2), after=None, limit=10) == []
        assert (
            await profits.page_between(
                date(2026, 10, 1),
                date(2026, 10, 2),
                after=(date(2026, 10, 1), 5),
                limit=10,
            )
            == []
        )

    asyncio.run(_read())
    assert len(session.statements) == 5
    compiled = " ".join(str(item) for item in session.statements)
    assert "idempotency_key" in compiled
    assert "stat_date" in compiled

    session.fail = "uq_report_export_tenant_idempotency"
    assert asyncio.run(exports.insert(ReportExport())) == "idempotency"
    session.fail = "uq_report_export_tenant_active"
    assert asyncio.run(exports.insert(ReportExport())) == "active"
    session.fail = "something else"
    with pytest.raises(IntegrityError):
        asyncio.run(exports.insert(ReportExport()))

    assert _integrity_kind(IntegrityError("x", {}, Exception("uq_report_export_tenant_idempotency"))) == "idempotency"


def test_directory_store_rejects_parent_keys(tmp_path: Path) -> None:
    store = DirectoryObjectStore(tmp_path)
    assert store.put("9/1.csv", b"a,b", "text/csv") == "9/1.csv"
    assert store.get("9/1.csv") == b"a,b"
    assert store.get("missing.csv") is None
    store.delete("9/1.csv")
    assert store.get("9/1.csv") is None
    with pytest.raises(ValueError):
        store.put("../secret.csv", b"no", "text/csv")
    with pytest.raises(ValueError):
        store.get("/etc/passwd")


def test_export_store_falls_back_to_a_local_directory(tmp_path: Path) -> None:
    class _Down:
        def put(self, key: str, body: bytes, content_type: str) -> str:
            del key, body, content_type
            raise OSError("minio down")

        def get(self, key: str) -> bytes | None:
            del key
            raise OSError("minio down")

        def delete(self, key: str) -> None:
            del key
            raise OSError("minio down")

    local = DirectoryObjectStore(tmp_path)
    store = ResilientExportStore()
    store._local = local
    store._remote = _Down()  # type: ignore[assignment]
    assert store.put("1/2.csv", b"csv", "text/csv") == "1/2.csv"
    assert store._remote is None
    assert store.get("1/2.csv") == b"csv"
    store.delete("1/2.csv")
    assert store.get("1/2.csv") is None

    store._remote = _Down()  # type: ignore[assignment]
    assert store.get("absent.csv") is None
    store._remote = _Down()  # type: ignore[assignment]
    store.delete("absent.csv")
    assert isinstance(export_object_store(), ResilientExportStore)
    assert default_object_store() is not None


def test_s3_store_reads_bytes_and_treats_a_missing_key_as_empty() -> None:
    class _Body:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def read(self) -> object:
            return self.payload

    class _Client:
        def __init__(self) -> None:
            self.mode = "ok"

        def put_object(self, **kwargs: object) -> dict[str, str]:
            del kwargs
            return {}

        def delete_object(self, **kwargs: object) -> dict[str, str]:
            del kwargs
            return {}

        def get_object(self, **kwargs: object) -> dict[str, _Body]:
            del kwargs
            if self.mode == "missing":
                raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
            if self.mode == "denied":
                raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
            if self.mode == "text":
                return {"Body": _Body("nope")}
            return {"Body": _Body(b"file")}

    client = _Client()
    store = S3ObjectStore("exports", connect_timeout=2)
    store._client = lambda: client  # type: ignore[method-assign]
    assert store.put("k", b"file", "text/csv") == "k"
    assert store.get("k") == b"file"
    store.delete("k")
    client.mode = "missing"
    assert store.get("k") is None
    client.mode = "denied"
    with pytest.raises(ClientError):
        store.get("k")
    client.mode = "text"
    with pytest.raises(TypeError):
        store.get("k")
    built = S3ObjectStore("exports", connect_timeout=1)._client()
    assert built is not None


def test_memory_store_replaces_and_drops_objects() -> None:
    store = MemoryObjectStore()
    assert store.put("a", b"1", "text/csv") == "a"
    assert store.get("a") == b"1"
    assert store.get("b") is None
    store.delete("a")
    store.delete("missing")
    assert store.get("a") is None
