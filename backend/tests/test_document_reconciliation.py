import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.batch import (
    BatchStatus,
    DocumentType,
    ProcessingBatch,
    ProcessingStatus,
    SourceDocument,
    SourceFolder,
)
from app.services import document_reconciliation_service as reconciliation_service
from app.services.document_reconciliation_service import (
    build_candidate_groups,
    reconcile_batch,
    reconcile_records,
)
from app.services.expense_record_service import build_expense_records


def _record(
    record_id: str,
    *,
    record_type: str,
    amount: str,
    counterparty: str = "测试商户",
    transaction_number: str | None = None,
    invoice_number: str | None = None,
) -> dict:
    return {
        "record_id": record_id,
        "record_type": record_type,
        "document_date": "2025-03-05",
        "counterparty": counterparty,
        "description": "办公用品订单",
        "invoice_number": invoice_number,
        "order_number": None,
        "transaction_number": transaction_number,
        "primary_amount": {
            "value": amount,
            "evidence": f"实付 {amount} 元",
            "kind": "PAID",
        },
        "item_subtotal": {"value": None, "evidence": None, "kind": "UNKNOWN"},
        "original_amount": {"value": None, "evidence": None, "kind": "UNKNOWN"},
        "discount_amount": {"value": None, "evidence": None, "kind": "UNKNOWN"},
        "items": [],
        "trip": None,
        "transfer": None,
        "manual_review": False,
        "review_reasons": [],
        "confidence": 0.98,
    }


def _document(
    document_id: int,
    source_folder: SourceFolder,
    records: list[dict],
) -> SourceDocument:
    now = datetime(2025, 3, 6, 9, 0, 0)
    return SourceDocument(
        id=document_id,
        batch_id=10,
        source_folder=source_folder,
        relative_path=f"{source_folder.value}/{document_id}.png",
        file_name=f"{document_id}.png",
        file_type="png",
        file_size=100,
        file_hash=f"hash-{document_id}",
        document_type=(
            DocumentType.ELECTRONIC_INVOICE
            if source_folder != SourceFolder.PAYMENT_EVIDENCE
            else DocumentType.PAYMENT_SCREENSHOT
        ),
        processing_status=ProcessingStatus.RECOGNIZED,
        document_date=date(2025, 3, 5),
        counterparty="测试商户",
        amount=sum(
            (Decimal(record["primary_amount"]["value"]) for record in records),
            Decimal("0.00"),
        ),
        extracted_fields_json={"records": records},
        processing_attempts=1,
        created_at=now,
        updated_at=now,
    )


class _Provider:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def chat_completion(self, _system, _prompt, **_kwargs):
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


class _Llm:
    def __init__(self, provider=None):
        self.active_provider = provider
        self.is_available = provider is not None


def test_one_image_with_multiple_records_builds_one_view_per_transaction():
    document = _document(
        1,
        SourceFolder.PAYMENT_EVIDENCE,
        [
            _record("R1", record_type="ORDER", amount="10.00"),
            _record("R2", record_type="ORDER", amount="20.00"),
            _record("R3", record_type="ORDER", amount="30.00"),
        ],
    )

    views = build_expense_records([document])

    assert [view.record_ref for view in views] == ["1:R1", "1:R2", "1:R3"]
    assert [view.amount for view in views] == [
        Decimal("10.00"),
        Decimal("20.00"),
        Decimal("30.00"),
    ]


def test_candidate_generation_requires_cross_file_exact_or_compound_evidence():
    documents = [
        _document(
            1,
            SourceFolder.INVOICE,
            [_record("R1", record_type="INVOICE", amount="24.99")],
        ),
        _document(
            2,
            SourceFolder.PAYMENT_EVIDENCE,
            [_record("R1", record_type="PAYMENT", amount="24.99")],
        ),
    ]

    groups = build_candidate_groups(build_expense_records(documents))

    assert len(groups) == 1
    assert {record.record_ref for record in groups[0].records} == {"1:R1", "2:R1"}
    assert "金额一致" in groups[0].candidate_evidence


def test_confirmed_duplicate_only_primary_record_enters_total(monkeypatch):
    documents = [
        _document(
            1,
            SourceFolder.INVOICE,
            [_record("R1", record_type="INVOICE", amount="24.99")],
        ),
        _document(
            2,
            SourceFolder.PAYMENT_EVIDENCE,
            [_record("R1", record_type="PAYMENT", amount="24.99")],
        ),
    ]
    provider = _Provider(
        {
            "prompt_version": "1.0",
            "groups": [
                {
                    "group_id": "C1",
                    "relation": "confirmed_duplicate",
                    "record_refs": ["1:R1", "2:R1"],
                    "primary_record_ref": "1:R1",
                    "confidence": 0.96,
                    "evidence": "金额、日期、对方和事项一致",
                }
            ],
        }
    )
    monkeypatch.setattr(
        reconciliation_service,
        "get_llm_service",
        lambda: _Llm(provider),
    )

    states, summary = reconcile_records(build_expense_records(documents))

    assert states["1:R1"]["role"] == "PRIMARY"
    assert states["1:R1"]["eligible_amount"] == "24.99"
    assert states["2:R1"]["role"] == "AUXILIARY"
    assert states["2:R1"]["eligible_amount"] == "0.00"
    assert summary["eligible_total"] == "24.99"
    assert summary["confirmed_groups"] == 1


def test_possible_duplicate_leaves_candidate_amounts_unresolved(monkeypatch):
    documents = [
        _document(
            1,
            SourceFolder.INVOICE,
            [_record("R1", record_type="INVOICE", amount="50.00")],
        ),
        _document(
            2,
            SourceFolder.PAYMENT_EVIDENCE,
            [_record("R1", record_type="PAYMENT", amount="50.00")],
        ),
    ]
    provider = _Provider(
        {
            "prompt_version": "1.0",
            "groups": [
                {
                    "group_id": "C1",
                    "relation": "possible_duplicate",
                    "record_refs": ["1:R1", "2:R1"],
                    "primary_record_ref": None,
                    "confidence": 0.65,
                    "evidence": "金额相同但缺少共同号码",
                }
            ],
        }
    )
    monkeypatch.setattr(
        reconciliation_service,
        "get_llm_service",
        lambda: _Llm(provider),
    )

    states, summary = reconcile_records(build_expense_records(documents))

    assert states["1:R1"]["role"] == "UNRESOLVED"
    assert states["2:R1"]["eligible_amount"] is None
    assert summary["eligible_total"] is None
    assert summary["review_groups"] == 1


async def test_reconcile_batch_persists_state_in_existing_json(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(
        reconciliation_service,
        "get_llm_service",
        lambda: _Llm(),
    )
    try:
        async with maker() as db:
            batch = ProcessingBatch(
                employee_name="张三",
                expense_month=date(2025, 3, 1),
                status=BatchStatus.COMPLETED,
            )
            db.add(batch)
            await db.flush()
            document = _document(
                1,
                SourceFolder.PAYMENT_EVIDENCE,
                [_record("R1", record_type="PAYMENT", amount="10.00")],
            )
            document.batch_id = batch.id
            db.add(document)
            await db.commit()

            summary = await reconcile_batch(db, batch.id)
            await db.refresh(document)

            state = document.extracted_fields_json["_reconciliation"]
            assert state["version"] == "1.0"
            assert state["records"]["R1"]["role"] == "INDEPENDENT"
            assert state["records"]["R1"]["eligible_amount"] == "10.00"
            assert summary.eligible_total == Decimal("10.00")
    finally:
        await engine.dispose()
