from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook, load_workbook

from app.models.batch import (
    DocumentType,
    ProcessingBatch,
    ProcessingStatus,
    SourceDocument,
    SourceFolder,
)
from app.services.claim_completeness_service import audit_claims
from app.services.claim_workbook_service import (
    AUDIT_COLUMN_TITLE,
    ClaimRow,
    annotate_claim_workbook,
    parse_claim_workbook,
)
from app.services.package_ingest_service import classify_package_path, parse_month_folder_name


def _claim_workbook(
    rows: list[tuple],
    *,
    include_invoice_number: bool = True,
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    headers = ["时间", "申请人员", "支付主体", "费用类型", "事项", "金额"]
    if include_invoice_number:
        headers.extend(["专票/普票", "发票号码", "备注"])
    sheet.append(headers)
    for row in rows:
        sheet.append(list(row))
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _document(
    *,
    document_id: int,
    batch_id: int,
    folder: SourceFolder,
    amount: str | None,
    description: str,
    document_type: DocumentType = DocumentType.ELECTRONIC_INVOICE,
    status: ProcessingStatus = ProcessingStatus.RECOGNIZED,
    invoice_number: str | None = None,
    item_name: str | None = None,
) -> SourceDocument:
    return SourceDocument(
        id=document_id,
        batch_id=batch_id,
        source_folder=folder,
        relative_path=f"{'发票' if folder == SourceFolder.INVOICE else '支付凭证'}/{document_id}.pdf",
        file_name=f"{document_id}.pdf",
        file_type="pdf",
        file_size=10,
        file_hash=f"{document_id:064d}",
        document_type=document_type,
        processing_status=status,
        description=description,
        amount=Decimal(amount) if amount is not None else None,
        invoice_number=invoice_number,
        item_name=item_name,
    )


def test_parse_month_folder_names():
    first = parse_month_folder_name("马耿宾2025年4月报销发票")
    assert first is not None
    assert first.employee_name == "马耿宾"
    assert first.expense_month == date(2025, 4, 1)
    second = parse_month_folder_name("姓名-2026年1月-报销")
    assert second is not None
    assert second.employee_name == "姓名"
    assert second.expense_month == date(2026, 1, 1)
    assert parse_month_folder_name("random") is None


def test_classify_package_paths():
    kind, month, path = classify_package_path("马耿宾-报销/马耿宾-报销总表.xlsx")
    assert kind == "workbook"
    assert month is None
    assert path is None

    kind, month, path = classify_package_path("马耿宾-报销/报销使用事项.txt")
    assert kind == "skip"

    kind, month, path = classify_package_path(
        "马耿宾-报销/马耿宾2025年4月报销发票/四月报销汇总清单.xlsx"
    )
    assert kind == "skip"

    kind, month, path = classify_package_path(
        "马耿宾-报销/马耿宾2025年4月报销发票/发票/a.png"
    )
    assert kind == "document"
    assert month is not None and month.expense_month == date(2025, 4, 1)
    assert path is not None
    assert path.relative_path == "发票/a.png"

    kind, month, path = classify_package_path(
        "姓名-报销/姓名-2026年1月-报销/支付凭证/b.jpg"
    )
    assert kind == "document"
    assert month is not None and month.expense_month == date(2026, 1, 1)
    assert path is not None
    assert path.relative_path == "支付凭证/b.jpg"


def test_parse_claim_workbook_skips_empty_invoice_number_and_totals():
    payload = _claim_workbook(
        [
            (date(2025, 4, 1), "马耿宾", "德纳智谷", "交通费", "打车", 200),
            (date(2025, 4, 1), "马耿宾", "德纳智谷", "项目成本", "卡丁车测试物业地面清洁", 200),
            (None, None, None, None, "合计", 400),
        ]
    )
    parsed = parse_claim_workbook(payload)
    assert parsed.sheet_name == "Sheet1"
    assert len(parsed.rows) == 2
    assert parsed.rows[0].item == "打车"
    assert parsed.rows[0].amount == Decimal("200.00")
    assert parsed.rows[0].invoice_number == ""
    assert parsed.rows[0].expense_month == date(2025, 4, 1)


def test_annotate_claim_workbook_adds_audit_column_without_touching_notes():
    payload = _claim_workbook(
        [
            (date(2025, 4, 1), "马耿宾", "德纳智谷", "交通费", "打车", 200, None, None, "提交人备注"),
        ]
    )
    annotated = annotate_claim_workbook(
        payload,
        sheet_name="Sheet1",
        remarks_by_row={2: "通过"},
    )
    workbook = load_workbook(BytesIO(annotated))
    sheet = workbook.active
    assert sheet["I1"].value == "备注"
    assert sheet["I2"].value == "提交人备注"
    assert sheet["J1"].value == AUDIT_COLUMN_TITLE
    assert sheet["J2"].value == "通过"


def test_audit_unique_amount_and_missing_invoice():
    batch = ProcessingBatch(
        id=1,
        employee_name="马耿宾",
        expense_month=date(2025, 4, 1),
    )
    invoice = _document(
        document_id=1,
        batch_id=1,
        folder=SourceFolder.INVOICE,
        amount="64.00",
        description="物联卡路由器充值",
    )
    payment = _document(
        document_id=2,
        batch_id=1,
        folder=SourceFolder.PAYMENT_EVIDENCE,
        amount="64.00",
        description="物联卡路由器充值",
        document_type=DocumentType.PAYMENT_SCREENSHOT,
    )
    only_payment = _document(
        document_id=3,
        batch_id=1,
        folder=SourceFolder.PAYMENT_EVIDENCE,
        amount="8.00",
        description="停车费",
        document_type=DocumentType.PAYMENT_SCREENSHOT,
    )
    claims = [
        ClaimRow(
            sheet="Sheet1",
            excel_row=2,
            claim_date=date(2025, 4, 1),
            expense_month=date(2025, 4, 1),
            applicant="马耿宾",
            expense_type="项目成本",
            item="物联卡路由器充值",
            amount=Decimal("64.00"),
            invoice_number="",
        ),
        ClaimRow(
            sheet="Sheet1",
            excel_row=3,
            claim_date=date(2025, 4, 1),
            expense_month=date(2025, 4, 1),
            applicant="马耿宾",
            expense_type="交通费",
            item="停车费",
            amount=Decimal("8.00"),
            invoice_number="",
        ),
        ClaimRow(
            sheet="Sheet1",
            excel_row=4,
            claim_date=date(2025, 5, 1),
            expense_month=date(2025, 5, 1),
            applicant="马耿宾",
            expense_type="交通费",
            item="打车",
            amount=Decimal("85.35"),
            invoice_number="",
        ),
    ]
    results = audit_claims(
        employee_name="马耿宾",
        claims=claims,
        batches=[batch],
        documents=[invoice, payment, only_payment],
    )
    assert results[0].remark == "通过"
    assert results[1].remark == "缺少发票"
    assert results[2].remark == "总表月份在提交包中无对应文件夹"


def test_audit_amount_collision_uses_item_and_taxi_sum():
    batch = ProcessingBatch(
        id=1,
        employee_name="马耿宾",
        expense_month=date(2025, 4, 1),
    )
    taxi_invoices = [
        _document(
            document_id=10 + index,
            batch_id=1,
            folder=SourceFolder.INVOICE,
            amount=amount,
            description="高德打车电子发票",
            document_type=DocumentType.ELECTRONIC_INVOICE,
        )
        for index, amount in enumerate(("21.90", "37.75", "140.35"))
    ]
    taxi_payments = [
        _document(
            document_id=20 + index,
            batch_id=1,
            folder=SourceFolder.PAYMENT_EVIDENCE,
            amount=amount,
            description="高德打车电子行程单",
            document_type=DocumentType.ITINERARY,
        )
        for index, amount in enumerate(("21.90", "37.75", "140.35"))
    ]
    karting_payment = _document(
        document_id=30,
        batch_id=1,
        folder=SourceFolder.PAYMENT_EVIDENCE,
        amount="200.00",
        description="卡丁车测试物业地面清洁",
        document_type=DocumentType.PAYMENT_SCREENSHOT,
    )
    claims = [
        ClaimRow(
            sheet="Sheet1",
            excel_row=2,
            claim_date=date(2025, 4, 1),
            expense_month=date(2025, 4, 1),
            applicant="马耿宾",
            expense_type="交通费",
            item="打车",
            amount=Decimal("200.00"),
            invoice_number="",
        ),
        ClaimRow(
            sheet="Sheet1",
            excel_row=3,
            claim_date=date(2025, 4, 1),
            expense_month=date(2025, 4, 1),
            applicant="马耿宾",
            expense_type="项目成本",
            item="卡丁车测试物业地面清洁",
            amount=Decimal("200.00"),
            invoice_number="",
        ),
    ]
    results = audit_claims(
        employee_name="马耿宾",
        claims=claims,
        batches=[batch],
        documents=[*taxi_invoices, *taxi_payments, karting_payment],
    )
    assert results[0].remark == "通过"
    assert results[1].remark == "缺少发票"
    assert len(results[0].invoice_document_ids) == 3
