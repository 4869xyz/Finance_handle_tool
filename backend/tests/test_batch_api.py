from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from httpx import AsyncClient

from app.database import Base, get_db
from app.main import app
from app.routers.batches import (
    _append_classification_worksheet,
    _classification_headers,
)
from app.models.batch import (
    BatchStatus,
    DocumentType,
    ProcessingBatch,
    ProcessingStatus,
    SourceDocument,
    SourceFolder,
)
from app.models.company_profile import CompanyProfile
from app.services.exclusion_service import FilterDecision, FilterOutcome
from app.services.expense_classification_service import (
    ExpenseClassification,
    ExpenseClassificationStatus,
    build_expense_summary_groups,
)
from app.services.document_reconciliation_service import ReconciliationSummary
from app.services.audit_classification_service import (
    AuditClassificationMetrics,
    AuditClassificationResults,
)


async def test_manual_batch_reconciliation_endpoint_requires_terminal_batch(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with maker() as session:
            yield session

    calls = []

    async def fake_reconcile(_db, batch_id):
        calls.append(batch_id)
        return ReconciliationSummary(
            batch_id=batch_id,
            status="COMPLETED",
            total_records=3,
            confirmed_groups=1,
            review_groups=0,
            eligible_total=Decimal("88.50"),
            message="跨文件核对完成",
        )

    monkeypatch.setattr(
        "app.services.document_reconciliation_service.reconcile_batch",
        fake_reconcile,
    )
    app.dependency_overrides[get_db] = override_db
    try:
        async with maker() as db:
            db.add_all([
                ProcessingBatch(
                    id=1,
                    employee_name="张三",
                    expense_month=date(2025, 7, 1),
                    status=BatchStatus.PROCESSING,
                ),
                ProcessingBatch(
                    id=2,
                    employee_name="李四",
                    expense_month=date(2025, 7, 1),
                    status=BatchStatus.COMPLETED,
                ),
            ])
            await db.commit()

        async with AsyncClient(app=app, base_url="http://test") as client:
            active = await client.post("/api/batches/1/reconcile")
            assert active.status_code == 409
            completed = await client.post("/api/batches/2/reconcile")
            assert completed.status_code == 200
            assert completed.json() == {
                "batch_id": 2,
                "status": "COMPLETED",
                "total_records": 3,
                "confirmed_groups": 1,
                "review_groups": 0,
                "eligible_total": "88.50",
                "message": "跨文件核对完成",
            }
            assert calls == [2]
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_batch_api_create_query_and_atomic_mismatch(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with maker() as session:
            yield session

    processing_calls = []

    def unexpected_processing_call(**kwargs):
        processing_calls.append(kwargs)
        raise AssertionError("批次创建和重新处理请求不得直接执行文档识别")

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        "app.services.document_service.process_document_content",
        unexpected_processing_call,
    )
    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            created = await client.post(
                "/api/batches",
                data={
                    "employee_name": "张三",
                    "expense_month": "2025-03",
                    "relative_paths": "月度目录/发票/ordinary.pdf",
                },
                files=[
                    (
                        "files",
                        ("ordinary.pdf", b"%PDF-ordinary", "application/pdf"),
                    )
                ],
            )
            assert created.status_code == 202
            assert created.headers["location"] == "/api/batches/1"
            assert created.json()["expense_month"] == "2025-03"
            assert created.json()["status"] == "UPLOADED"

            batches = await client.get("/api/batches")
            assert batches.status_code == 200
            assert batches.json()["total"] == 1

            documents = await client.get("/api/batches/1/documents")
            assert documents.status_code == 200
            assert documents.json()["items"][0]["processing_status"] == "UPLOADED"
            assert "currency" not in documents.json()["items"][0]
            assert processing_calls == []

            async with maker() as db:
                batch = await db.get(ProcessingBatch, 1)
                document = await db.scalar(
                    select(SourceDocument).where(SourceDocument.batch_id == 1)
                )
                batch.status = BatchStatus.FAILED
                document.processing_status = ProcessingStatus.FAILED
                document.status_reason = "PROCESSING_ERROR"
                await db.commit()

            reprocessed = await client.post("/api/batches/1/reprocess")
            assert reprocessed.status_code == 202
            assert reprocessed.json()["scheduled_count"] == 1

            documents = await client.get("/api/batches/1/documents")
            assert documents.json()["items"][0]["processing_status"] == "UPLOADED"
            assert processing_calls == []

            mismatch = await client.post(
                "/api/batches",
                data={
                    "employee_name": "李四",
                    "expense_month": "2025-03",
                    "relative_paths": "发票/a.pdf",
                },
                files=[
                    ("files", ("a.pdf", b"a", "application/pdf")),
                    ("files", ("b.pdf", b"b", "application/pdf")),
                ],
            )
            assert mismatch.status_code == 400

        async with maker() as db:
            assert (
                await db.scalar(select(func.count()).select_from(ProcessingBatch))
                == 1
            )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_batch_excel_export_includes_all_documents_and_typed_values():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        async with maker() as db:
            batch = ProcessingBatch(
                employee_name='张三:/测试',
                expense_month=date(2025, 7, 1),
                status=BatchStatus.COMPLETED,
                total_files=2,
                recognized_files=1,
                skipped_files=1,
            )
            db.add(batch)
            await db.flush()
            db.add_all([
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.PAYMENT_EVIDENCE,
                    relative_path='支付凭证/行程.pdf',
                    file_name='行程.pdf',
                    file_type='pdf',
                    file_size=1024,
                    file_hash='a' * 64,
                    document_type=DocumentType.ITINERARY,
                    processing_status=ProcessingStatus.RECOGNIZED,
                    document_date=date(2025, 7, 5),
                    counterparty='高德地图',
                    description='网约车行程',
                    amount=Decimal('19.50'),
                    currency='CNY',
                    transaction_number='ORDER-001',
                    classification_confidence=Decimal('0.9500'),
                    extraction_confidence=Decimal('0.9100'),
                    processing_attempts=1,
                    updated_at=datetime(2025, 7, 6, 12, 30, 0),
                ),
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.PAYMENT_EVIDENCE,
                    relative_path='支付凭证/PDF合并.pdf',
                    file_name='PDF合并.pdf',
                    file_type='pdf',
                    file_size=2048,
                    file_hash='b' * 64,
                    document_type=DocumentType.UNKNOWN,
                    processing_status=ProcessingStatus.SKIPPED,
                    status_reason='DUPLICATE_SUMMARY_PDF',
                    status_detail='重复汇总文件',
                    processing_attempts=0,
                    updated_at=datetime(2025, 7, 6, 12, 31, 0),
                ),
            ])
            await db.commit()

        async with AsyncClient(app=app, base_url="http://test") as client:
            page = await client.get('/api/batches/1/documents?page=1&page_size=1')
            assert len(page.json()['items']) == 1

            response = await client.get('/api/batches/1/export/excel')
            assert response.status_code == 200
            assert response.headers['content-type'].startswith(
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            disposition = response.headers['content-disposition']
            assert 'filename*=UTF-8' in disposition
            assert '%E8%AF%86%E5%88%AB%E7%BB%93%E6%9E%9C.xlsx' in disposition

            workbook = load_workbook(BytesIO(response.content), data_only=True)
            worksheet = workbook['识别结果']
            assert worksheet.max_row == 3
            assert worksheet.max_column == 26
            assert worksheet.freeze_panes == 'A2'
            assert worksheet.auto_filter.ref == 'A1:Z3'
            assert [cell.value for cell in worksheet[1]][:5] == [
                '序号', '员工姓名', '报销月份', '批次号', '来源'
            ]
            assert "币种" not in [cell.value for cell in worksheet[1]]
            assert worksheet['P1'].value == '项目名称'
            assert worksheet['Q1'].value == '发票号码'
            assert worksheet['S1'].value == '订单号'
            assert worksheet['T1'].value == '行程起点'
            assert worksheet['I2'].value == '已识别'
            assert worksheet['L2'].value == datetime(2025, 7, 5)
            assert worksheet['O2'].value == 19.5
            assert worksheet['O2'].number_format == '#,##0.00'
            assert worksheet['W2'].value == 0.95
            assert worksheet['W2'].number_format == '0.0%'
            assert worksheet['X2'].value == 0.91
            assert worksheet['I3'].value == '已跳过'
            assert worksheet['J3'].value == '重复汇总PDF，已跳过'
            assert worksheet['A2'].fill.fgColor.rgb.endswith('1677FF') is False
            assert worksheet['A3'].fill.fgColor.rgb.endswith('FCE8E6')
            assert all(
                side.style == 'thin'
                for side in (
                    worksheet['A1'].border.left,
                    worksheet['A1'].border.right,
                    worksheet['A1'].border.top,
                    worksheet['A1'].border.bottom,
                    worksheet['Z3'].border.left,
                    worksheet['Z3'].border.right,
                    worksheet['Z3'].border.top,
                    worksheet['Z3'].border.bottom,
                )
            )

            missing = await client.get('/api/batches/999/export/excel')
            assert missing.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_filtered_batch_export_has_auditable_seven_sheet_reconciliation(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with maker() as session:
            yield session

    def fake_filter(documents, profile, reference=None):
        outcomes = [
            (FilterOutcome.KEEP, "BUSINESS_EXPENSE"),
            (FilterOutcome.KEEP, "BUSINESS_EXPENSE"),
            (FilterOutcome.EXCLUDE, "DUPLICATE_SUMMARY_PDF"),
            (FilterOutcome.REVIEW, "SOURCE_NOT_RECOGNIZED"),
            (FilterOutcome.KEEP, "BUSINESS_EXPENSE"),
        ]
        return [
            FilterDecision(
                document_id=document.id,
                outcome=outcome,
                reason_code=code,
                reason="测试原因",
                evidence="测试证据",
                buyer_name=(
                    "示例科技有限公司"
                    if index == 0
                    else "另一付款公司" if index == 4 else None
                ),
                buyer_tax_id=None,
                confidence=0.95 if outcome == FilterOutcome.KEEP else None,
                company_profile_version=profile.version if profile else None,
            )
            for index, (document, (outcome, code)) in enumerate(zip(documents, outcomes))
        ]

    def fake_classify(documents, reference=None):
        values = [
            ("办公费", "打印纸", "打印纸采购", ExpenseClassificationStatus.CLASSIFIED),
            ("办公费", "打印纸", "打印纸采购", ExpenseClassificationStatus.CLASSIFIED),
            ("待分类", "", "PDF合并", ExpenseClassificationStatus.UNCLASSIFIED),
            ("待分类", "", "订单凭证", ExpenseClassificationStatus.UNCLASSIFIED),
            ("办公费", "打印纸", "打印纸采购", ExpenseClassificationStatus.CLASSIFIED),
        ]
        return {
            document.id: ExpenseClassification(
                document_id=document.id,
                expense_type=expense_type,
                standard_subcategory=subcategory,
                expense_item=item,
                hospitality_target="",
                status=status,
                confidence=0.95 if status == ExpenseClassificationStatus.CLASSIFIED else None,
                evidence="测试分类依据",
                evidence_source="RULE",
                error_code=(
                    None
                    if status == ExpenseClassificationStatus.CLASSIFIED
                    else "SOURCE_NOT_RECOGNIZED"
                ),
            )
            for document, (expense_type, subcategory, item, status)
            in zip(documents, values)
        }

    async def fake_audit_classification(documents, records, profile, reference=None):
        decisions = fake_filter(records, profile, reference)
        classifications = fake_classify(records, reference)
        return AuditClassificationResults(
            decisions={decision.document_id: decision for decision in decisions},
            classifications=classifications,
            metrics=AuditClassificationMetrics(
                total_records=len(records),
                cache_hit_count=0,
                cache_miss_count=len(records),
                llm_batch_count=1,
                llm_request_count=1,
                retry_count=0,
                elapsed_ms=1,
            ),
        )

    monkeypatch.setattr(
        "app.routers.batches.ensure_audit_classification_results",
        fake_audit_classification,
    )
    async def reconcile_must_not_run(*_args, **_kwargs):
        raise AssertionError("普通导出不应自动执行跨文件核对")

    monkeypatch.setattr(
        "app.routers.batches.reconcile_batch",
        reconcile_must_not_run,
        raising=False,
    )
    app.dependency_overrides[get_db] = override_db
    try:
        async with maker() as db:
            batch = ProcessingBatch(
                employee_name="张三",
                expense_month=date(2025, 7, 1),
                status=BatchStatus.COMPLETED,
                total_files=5,
                recognized_files=3,
                manual_review_files=1,
                skipped_files=1,
            )
            db.add(batch)
            db.add(CompanyProfile(
                official_name="示例科技有限公司",
                aliases=[],
                tax_ids=["913000000000000001"],
            ))
            await db.flush()
            db.add_all([
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.INVOICE,
                    relative_path="发票/a.pdf",
                    file_name="a.pdf",
                    file_type="pdf",
                    file_size=1,
                    file_hash="a" * 64,
                    document_type=DocumentType.ELECTRONIC_INVOICE,
                    processing_status=ProcessingStatus.RECOGNIZED,
                    amount=Decimal("20.00"),
                    currency="CNY",
                    processing_attempts=1,
                ),
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.PAYMENT_EVIDENCE,
                    relative_path="支付凭证/b.jpg",
                    file_name="b.jpg",
                    file_type="jpg",
                    file_size=1,
                    file_hash="b" * 64,
                    document_type=DocumentType.PAYMENT_SCREENSHOT,
                    processing_status=ProcessingStatus.RECOGNIZED,
                    amount=Decimal("30.00"),
                    currency="CNY",
                    processing_attempts=1,
                ),
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.REPLACEMENT_INVOICE,
                    relative_path="补票/PDF合并.pdf",
                    file_name="PDF合并.pdf",
                    file_type="pdf",
                    file_size=1,
                    file_hash="c" * 64,
                    document_type=DocumentType.UNKNOWN,
                    processing_status=ProcessingStatus.SKIPPED,
                    status_reason="DUPLICATE_SUMMARY_PDF",
                    currency="USD",
                    processing_attempts=0,
                ),
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.PAYMENT_EVIDENCE,
                    relative_path="支付凭证/d.jpg",
                    file_name="d.jpg",
                    file_type="jpg",
                    file_size=1,
                    file_hash="d" * 64,
                    document_type=DocumentType.ORDER_SCREENSHOT,
                    processing_status=ProcessingStatus.NEEDS_MANUAL_REVIEW,
                    amount=Decimal("12.00"),
                    processing_attempts=1,
                ),
                SourceDocument(
                    batch_id=batch.id,
                    source_folder=SourceFolder.INVOICE,
                    relative_path="发票/e.pdf",
                    file_name="e.pdf",
                    file_type="pdf",
                    file_size=1,
                    file_hash="e" * 64,
                    document_type=DocumentType.ELECTRONIC_INVOICE,
                    processing_status=ProcessingStatus.RECOGNIZED,
                    amount=Decimal("5.00"),
                    currency="CNY",
                    processing_attempts=1,
                ),
            ])
            await db.commit()

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/batches/1/export/filtered")
            assert response.status_code == 200
            assert "%E5%89%94%E9%99%A4%E7%BB%93%E6%9E%9C.xlsx" in response.headers[
                "content-disposition"
            ]

            workbook = load_workbook(BytesIO(response.content), data_only=True)
            assert workbook.sheetnames == [
                "识别结果",
                "发票补票_剔除后",
                "支付凭证_剔除后",
                "排除与待复核",
                "发票补票_分类汇总",
                "支付凭证_分类汇总",
                "分类审计明细",
            ]
            original = workbook["识别结果"]
            invoice = workbook["发票补票_剔除后"]
            payment = workbook["支付凭证_剔除后"]
            exception = workbook["排除与待复核"]
            invoice_classification = workbook["发票补票_分类汇总"]
            payment_classification = workbook["支付凭证_分类汇总"]
            classification_audit = workbook["分类审计明细"]
            assert original.max_row == 6
            assert original.max_column == 26
            assert invoice.max_row == 4
            assert payment.max_row == 3
            assert exception.max_row == 3
            assert invoice.max_column == payment.max_column == exception.max_column == 37
            assert "币种" not in [cell.value for cell in original[1]]
            assert "币种" not in [cell.value for cell in invoice[1]]
            assert invoice["AA1"].value == "筛选结论"
            assert invoice["AJ1"].value == "辅助表版本"
            assert invoice["AK1"].value == "辅助匹配依据"
            assert invoice["AA2"].value == "保留"
            assert invoice["A2"].font.color.type != "rgb"
            assert original["A4"].font.color.type != "rgb"
            assert original["A2"].fill.fgColor.rgb.endswith("FCE8E6") is False
            assert original["A3"].fill.fgColor.rgb.endswith("FCE8E6") is False
            assert original["A4"].fill.fgColor.rgb.endswith("FCE8E6")
            assert original["A5"].fill.fgColor.rgb.endswith("FCE8E6")
            assert original["A6"].fill.fgColor.rgb.endswith("FCE8E6") is False
            assert invoice["F3"].value == "PDF合并.pdf"
            assert invoice["AA3"].value == "排除"
            assert all(
                cell.font.color.type == "rgb"
                and cell.font.color.rgb.endswith("C00000")
                for cell in invoice[3]
            )
            assert payment["F3"].value == "d.jpg"
            assert payment["AA3"].value == "待复核"
            assert all(
                cell.font.color.type == "rgb"
                and cell.font.color.rgb.endswith("C00000")
                for cell in payment[3]
            )
            assert exception["AA2"].value == "排除"
            assert exception["AA3"].value == "待复核"
            assert exception["A2"].fill.fgColor.rgb.endswith("FCE8E6")
            assert exception["A3"].fill.fgColor.rgb.endswith("FFF4CC")
            assert original.max_row - 1 == (
                invoice.max_row - 1 + payment.max_row - 1
            )
            assert exception.max_row - 1 == 2
            assert invoice_classification.max_column == 14
            assert payment_classification.max_column == 14
            assert invoice_classification["A1"].value == "序号"
            assert invoice_classification["D1"].value == "费用类型"
            assert invoice_classification["H1"].value == "发票号码"
            assert payment_classification["H1"].value == "交易/订单号"
            assert invoice_classification["K1"].value == "申请人"
            assert invoice_classification["L1"].value == "费用类型"
            assert invoice_classification["M1"].value == "费用详细事项"
            assert invoice_classification["N1"].value == "合计金额"
            assert "支付主体" not in [
                cell.value for cell in invoice_classification[1]
            ]
            assert "差额（发票-支付）" not in [
                cell.value for cell in invoice_classification[1]
            ]
            assert "核对状态" not in [cell.value for cell in invoice_classification[1]]
            assert "币种" not in [cell.value for cell in invoice_classification[1]]
            assert "币种" not in [cell.value for cell in payment_classification[1]]
            assert classification_audit.max_row == 6
            assert classification_audit.max_column == 21
            assert classification_audit["G1"].value == "分类状态"
            assert classification_audit["O1"].value == "分类规则版本"
            assert classification_audit["P1"].value == "记录引用"
            assert classification_audit["Q1"].value == "跨文件关系"
            assert classification_audit["R1"].value == "核对角色"
            assert classification_audit["U1"].value == "去重计入金额"
            assert [
                cell.value
                for cell in invoice_classification["A"]
                if cell.row > 1 and cell.value is not None
            ] == [1, 2, 3]
            assert [
                cell.value
                for cell in payment_classification["A"]
                if cell.row > 1 and cell.value is not None
            ] == [1, 2]
            invoice_duplicate_row = next(
                row
                for row in invoice_classification.iter_rows(min_row=2)
                if row[8].value == "PDF合并.pdf"
            )
            payment_review_row = next(
                row
                for row in payment_classification.iter_rows(min_row=2)
                if row[8].value == "d.jpg"
            )
            assert all(
                cell.font.color.type == "rgb"
                and cell.font.color.rgb.endswith("C00000")
                for cell in invoice_duplicate_row[:10]
            )
            assert all(
                cell.font.color.type == "rgb"
                and cell.font.color.rgb.endswith("C00000")
                for cell in payment_review_row[:10]
            )
            merged_ranges = {
                str(cell_range) for cell_range in invoice_classification.merged_cells.ranges
            }
            assert {f"{column}2:{column}3" for column in "KLMN"} <= merged_ranges
            assert not any("4" in cell_range for cell_range in merged_ranges)
            assert invoice_classification["K2"].value == "张三"
            assert invoice_classification["L2"].value == "办公费 / 打印纸"
            assert invoice_classification["M2"].value == "打印纸采购"
            assert invoice_classification["N2"].value == 25
            assert payment_classification["K2"].value == "张三"
            assert payment_classification["L2"].value == "办公费 / 打印纸"
            assert payment_classification["N2"].value == 30
            assert invoice_classification["L4"].value == "待分类"
            assert invoice_classification["N4"].value is None
            assert invoice_classification["N4"].comment is not None
            assert payment_classification["L3"].value == "待分类"
            assert "币种" not in (payment_classification["J3"].value or "")
            assert payment_classification["N3"].value == 12
            assert payment_classification["N3"].comment is None
            assert classification_audit["I2"].value == "测试分类依据"
            assert classification_audit["J2"].value == "RULE"
            for worksheet in workbook.worksheets:
                assert worksheet.freeze_panes == "A2"
                for row in worksheet.iter_rows():
                    for cell in row:
                        if isinstance(cell, MergedCell):
                            continue
                        assert cell.border.left.style == "thin"
                        assert cell.border.right.style == "thin"
                        assert cell.border.top.style == "thin"
                        assert cell.border.bottom.style == "thin"
                for merged_range in worksheet.merged_cells.ranges:
                    top_left = worksheet.cell(merged_range.min_row, merged_range.min_col)
                    bottom_left = worksheet.cell(merged_range.max_row, merged_range.min_col)
                    top_right = worksheet.cell(merged_range.min_row, merged_range.max_col)
                    assert top_left.border.top.style == "thin"
                    assert top_left.border.left.style == "thin"
                    assert bottom_left.border.bottom.style == "thin"
                    assert top_right.border.right.style == "thin"
            assert invoice_classification.auto_filter.ref is None
            assert payment_classification.auto_filter.ref is None
            for worksheet in (original, invoice, payment, exception, classification_audit):
                assert worksheet.auto_filter.ref is not None
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def test_classification_group_writes_summary_without_payment_subject():
    batch = ProcessingBatch(
        id=1,
        employee_name="张三",
        expense_month=date(2025, 7, 1),
        status=BatchStatus.COMPLETED,
    )
    document = SourceDocument(
        id=1,
        batch_id=1,
        source_folder=SourceFolder.INVOICE,
        relative_path="发票/a.pdf",
        file_name="a.pdf",
        file_type="pdf",
        file_size=1,
        file_hash="a" * 64,
        document_type=DocumentType.ELECTRONIC_INVOICE,
        processing_status=ProcessingStatus.RECOGNIZED,
        amount=Decimal("10.00"),
        currency="CNY",
    )
    decision = FilterDecision(
        document_id=1,
        outcome=FilterOutcome.KEEP,
        reason_code="BUSINESS_EXPENSE",
        reason="测试原因",
        evidence="测试证据",
        buyer_name=None,
        buyer_tax_id=None,
        confidence=0.95,
        company_profile_version=None,
    )
    classification = ExpenseClassification(
        document_id=1,
        expense_type="办公费",
        standard_subcategory="文具",
        expense_item="签字笔",
        hospitality_target="",
        status=ExpenseClassificationStatus.CLASSIFIED,
        confidence=0.95,
        evidence="测试分类依据",
        evidence_source="RULE",
    )
    classifications = {1: classification}
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(_classification_headers(SourceFolder.INVOICE))

    row_count = _append_classification_worksheet(
        worksheet,
        batch=batch,
        documents=[document],
        decision_by_document={1: decision},
        classification_by_document=classifications,
        groups=build_expense_summary_groups([document], classifications),
        source_folder=SourceFolder.INVOICE,
    )

    assert row_count == 1
    assert not worksheet.merged_cells.ranges
    assert worksheet.max_column == 14
    assert "支付主体" not in [cell.value for cell in worksheet[1]]
    assert worksheet["K2"].value == "张三"
    assert worksheet["L2"].value == "办公费 / 文具"
    assert worksheet["M2"].value == "签字笔"
    assert worksheet["N2"].value == 10
    assert worksheet.auto_filter.ref is None


def test_classification_group_displays_available_total_when_one_amount_is_missing():
    batch = ProcessingBatch(
        id=1,
        employee_name="张三",
        expense_month=date(2025, 7, 1),
        status=BatchStatus.COMPLETED,
    )
    documents = [
        SourceDocument(
            id=document_id,
            batch_id=1,
            source_folder=SourceFolder.INVOICE,
            relative_path=f"发票/{document_id}.pdf",
            file_name=f"{document_id}.pdf",
            file_type="pdf",
            file_size=1,
            file_hash=str(document_id) * 64,
            document_type=DocumentType.ELECTRONIC_INVOICE,
            processing_status=ProcessingStatus.RECOGNIZED,
            description="打印纸采购",
            amount=amount,
        )
        for document_id, amount in ((1, Decimal("10.00")), (2, None))
    ]
    classifications = {
        document.id: ExpenseClassification(
            document_id=document.id,
            expense_type="办公费",
            standard_subcategory="打印纸",
            expense_item="打印纸采购",
            hospitality_target="",
            status=ExpenseClassificationStatus.CLASSIFIED,
            confidence=0.95,
            evidence="测试分类依据",
            evidence_source="RULE",
        )
        for document in documents
    }
    decisions = {
        document.id: FilterDecision(
            document_id=document.id,
            outcome=FilterOutcome.KEEP,
            reason_code="BUSINESS_EXPENSE",
            reason="测试原因",
            evidence="测试证据",
            buyer_name=None,
            buyer_tax_id=None,
            confidence=0.95,
            company_profile_version=None,
        )
        for document in documents
    }
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(_classification_headers(SourceFolder.INVOICE))

    _append_classification_worksheet(
        worksheet,
        batch=batch,
        documents=documents,
        decision_by_document=decisions,
        classification_by_document=classifications,
        groups=build_expense_summary_groups(documents, classifications),
        source_folder=SourceFolder.INVOICE,
    )

    assert worksheet["N2"].value == 10
    assert worksheet["N2"].fill.fgColor.rgb.endswith("FCE8E6")
    assert "仅包含已识别金额" in worksheet["N2"].comment.text


async def test_review_reference_upload_versions_list_and_download():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with maker() as session:
            yield session

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "报销明细"
    worksheet.append(["发票号", "用途", "金额"])
    worksheet.append(["INV-2025-001", "办公用品", Decimal("88.50")])
    stream = BytesIO()
    workbook.save(stream)
    content = stream.getvalue()

    app.dependency_overrides[get_db] = override_db
    try:
        async with maker() as db:
            db.add(ProcessingBatch(
                employee_name="张三",
                expense_month=date(2025, 7, 1),
                status=BatchStatus.COMPLETED,
                total_files=0,
            ))
            await db.commit()

        async with AsyncClient(app=app, base_url="http://test") as client:
            first = await client.post(
                "/api/batches/1/review-references",
                files={"file": ("明细.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
            assert first.status_code == 201
            assert first.json()["version"] == 1
            assert first.json()["row_count"] == 2

            second = await client.post(
                "/api/batches/1/review-references",
                files={"file": ("明细修订.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
            assert second.status_code == 201
            assert second.json()["version"] == 2

            listing = await client.get("/api/batches/1/review-references")
            assert listing.status_code == 200
            items = listing.json()["items"]
            assert [item["version"] for item in items] == [2, 1]
            assert items[0]["is_current"] is True
            assert items[1]["is_current"] is False

            download = await client.get(
                "/api/batches/1/review-references/1/download"
            )
            assert download.status_code == 200
            assert download.content == content

            invalid = await client.post(
                "/api/batches/1/review-references",
                files={"file": ("错误.xls", b"not-xlsx", "application/vnd.ms-excel")},
            )
            assert invalid.status_code == 400
            after_invalid = await client.get("/api/batches/1/review-references")
            assert len(after_invalid.json()["items"]) == 2
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
