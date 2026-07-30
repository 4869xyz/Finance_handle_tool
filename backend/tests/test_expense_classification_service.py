from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from app.models.batch import (
    DocumentType,
    ProcessingStatus,
    SourceDocument,
    SourceFolder,
)
from app.models.review_reference import BatchReviewReference
from app.services import expense_classification_service as service
from app.services.expense_classification_service import (
    ExpenseClassificationStatus,
    build_expense_summary_groups,
    classification_risk_reason,
    classify_expense_by_rule,
    classify_expenses,
    normalize_expense_type,
)
from app.services.review_reference_service import parse_review_reference


def document(
    document_id: int = 1,
    *,
    source_folder: SourceFolder = SourceFolder.INVOICE,
    document_type: DocumentType = DocumentType.ELECTRONIC_INVOICE,
    description: str = "",
    amount: str | None = "100.00",
    currency: str | None = "CNY",
    status: ProcessingStatus = ProcessingStatus.RECOGNIZED,
) -> SourceDocument:
    return SourceDocument(
        id=document_id,
        batch_id=1,
        source_folder=source_folder,
        relative_path=f"资料/{document_id}.pdf",
        file_name=f"{description or document_id}.pdf",
        file_type="pdf",
        file_size=1,
        file_hash=str(document_id) * 64,
        document_type=document_type,
        processing_status=status,
        description=description,
        document_date=date(2025, 7, 8),
        counterparty="测试商户",
        amount=Decimal(amount) if amount is not None else None,
        currency=currency,
        processing_attempts=1,
    )


def test_same_transport_rule_is_used_for_invoice_and_payment():
    invoice = document(
        description="本月滴滴打车",
        document_type=DocumentType.TAXI_INVOICE,
    )
    payment = document(
        2,
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
        document_type=DocumentType.PAYMENT_SCREENSHOT,
        description="本月滴滴打车",
    )

    invoice_result = classify_expense_by_rule(invoice)
    payment_result = classify_expense_by_rule(payment)

    assert invoice_result.expense_type == payment_result.expense_type == "交通费"
    assert invoice_result.standard_subcategory == payment_result.standard_subcategory == "打车"


def test_employee_meal_is_welfare_and_hospitality_requires_target():
    welfare = classify_expense_by_rule(document(description="程序组加班点餐"))
    hospitality = classify_expense_by_rule(document(2, description="客户接待吃饭"))

    assert welfare.expense_type == "职工福利费"
    assert welfare.standard_subcategory == "员工活动"
    assert hospitality.expense_type == "招待费"
    assert hospitality.standard_subcategory == "餐饮"
    assert hospitality.hospitality_target == "待补充"
    assert hospitality.status == ExpenseClassificationStatus.HOSPITALITY_TARGET_REQUIRED


def test_expense_type_normalization_uses_welfare_name_and_rejects_totals():
    assert normalize_expense_type("福利费") == "职工福利费"
    assert normalize_expense_type("员工福利费") == "职工福利费"
    assert normalize_expense_type("合计") == "待分类"
    assert normalize_expense_type("会计") == "待分类"


def test_explicit_transport_takes_priority_over_employee_context():
    result = classify_expense_by_rule(document(description="代加班组员打车"))

    assert result.expense_type == "交通费"
    assert result.standard_subcategory == "打车"


def test_explicit_hospitality_target_is_kept():
    result = classify_expense_by_rule(
        document(description="客户餐饮 招待对象：北京客户有限公司")
    )

    assert result.expense_type == "招待费"
    assert result.hospitality_target == "北京客户有限公司"
    assert result.status == ExpenseClassificationStatus.CLASSIFIED


def test_strong_reference_match_preserves_custom_expense_type_and_item():
    invoice = document(description="研发采购")
    invoice.invoice_number = "INV-2025-001"
    reference = BatchReviewReference(
        version=3,
        parsed_rows=[{
            "sheet": "明细",
            "row": 2,
            "text": "INV-2025-001 | 项目成本 | 语音模块采购",
            "normalized": "inv2025001项目成本语音模块采购",
            "fields": {
                "expense_type": "项目成本",
                "expense_item": "语音模块采购",
            },
        }],
    )

    result = classify_expenses([invoice], reference)[invoice.id]

    assert result.expense_type == "项目成本"
    assert result.expense_item == "语音模块采购"
    assert result.evidence_source == "AUXILIARY_TABLE"
    assert result.reference_version == 3
    assert result.reconciliation_key == "辅助表v3|明细|第2行"


def test_reference_total_label_is_ignored_and_document_is_reclassified():
    invoice = document(description="打印纸采购")
    invoice.invoice_number = "INV-TOTAL-001"
    reference = BatchReviewReference(
        version=4,
        parsed_rows=[{
            "sheet": "汇总",
            "row": 2,
            "text": "INV-TOTAL-001 | 合计 | 打印纸采购",
            "normalized": "invtotal001合计打印纸采购",
            "fields": {
                "expense_type": "合计",
                "expense_item": "打印纸采购",
            },
        }],
    )

    result = classify_expenses([invoice], reference)[invoice.id]

    assert result.expense_type == "办公费"
    assert result.evidence_source == "RULE"


def test_reference_parser_keeps_classification_columns_from_sample_shape():
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "报销明细"
    worksheet.append(["时间", "申请人员", "费用类型", "事项", "金额", "招待对象"])
    worksheet.append([date(2025, 7, 1), "张三", "招待费", "客户餐饮", 200, "示例客户"])
    stream = BytesIO()
    workbook.save(stream)

    parsed = parse_review_reference(stream.getvalue(), "报销明细.xlsx")
    data_row = next(row for row in parsed.rows if row["row"] == 2)

    assert data_row["fields"] == {
        "expense_type": "招待费",
        "expense_item": "客户餐饮",
        "hospitality_target": "示例客户",
    }


def test_model_unavailable_only_leaves_unresolved_documents_unclassified(monkeypatch):
    class FakeLlmService:
        is_available = False
        active_provider = None

    monkeypatch.setattr(service, "get_llm_service", lambda: FakeLlmService())
    unresolved = document(description="一次无法判断的购买")
    ruled = document(2, description="打印纸采购")

    results = classify_expenses([unresolved, ruled])

    assert results[unresolved.id].expense_type == "待分类"
    assert results[unresolved.id].error_code == "CLASSIFICATION_MODEL_UNAVAILABLE"
    assert results[ruled.id].expense_type == "办公费"
    assert results[ruled.id].status == ExpenseClassificationStatus.CLASSIFIED


def test_low_confidence_model_suggestion_is_kept_but_marked_for_review(monkeypatch):
    class FakeProvider:
        calls = []

        def chat_completion(self, _system, _prompt, **kwargs):
            self.calls.append(kwargs)
            return (
                '{"items":[{"document_id":1,"expense_type":"差旅费",'
                '"standard_subcategory":"","expense_item":"境外用品",'
                '"hospitality_target":null,"confidence":0.60,'
                '"evidence":"摘要提到境外用品","evidence_source":"DESCRIPTION"}]}'
            )

    class FakeLlmService:
        is_available = True
        active_provider = FakeProvider()

    monkeypatch.setattr(service, "get_llm_service", lambda: FakeLlmService())

    result = classify_expenses([document(description="一次无法判断的购买")])[1]

    assert result.expense_type == "差旅费"
    assert result.status == ExpenseClassificationStatus.REVIEW
    assert result.error_code == "LOW_CLASSIFICATION_CONFIDENCE"
    assert FakeLlmService.active_provider.calls == [
        {"max_output_tokens": 4096, "json_mode": True}
    ]


def test_forbidden_model_expense_types_become_unclassified(monkeypatch):
    class FakeProvider:
        def __init__(self):
            self.expense_type = "合计"

        def chat_completion(self, _system, _prompt, **_kwargs):
            return (
                '{"items":[{"document_id":1,"expense_type":"'
                + self.expense_type
                + '","standard_subcategory":"","expense_item":"采购",'
                '"hospitality_target":null,"confidence":0.99,'
                '"evidence":"模型判断","evidence_source":"DOCUMENT_BODY"}]}'
            )

    provider = FakeProvider()

    class FakeLlmService:
        is_available = True
        active_provider = provider

    monkeypatch.setattr(service, "get_llm_service", lambda: FakeLlmService())

    for forbidden_type in ("合计", "会计"):
        provider.expense_type = forbidden_type
        result = classify_expenses([document(description="一次无法判断的购买")])[1]
        assert result.expense_type == "待分类"
        assert result.status == ExpenseClassificationStatus.UNCLASSIFIED
        assert result.error_code == "INVALID_CLASSIFICATION_RESPONSE"


def test_summary_groups_use_screenshot_category_order_before_office_fee():
    documents = [
        document(1, description="出差住宿"),
        document(2, description="滴滴打车"),
        document(3, description="顺丰快递"),
        document(4, description="货拉拉物流"),
        document(5, description="项目采购服务器"),
        document(6, description="客户接待吃饭"),
        document(7, description="程序组加班点餐"),
        document(8, description="打印纸采购"),
    ]
    classifications = {
        item.id: classify_expense_by_rule(item)
        for item in documents
    }

    groups = build_expense_summary_groups(documents, classifications)

    assert [group.expense_type for group in groups] == [
        "差旅费",
        "交通费",
        "快递费",
        "物流费",
        "项目成本",
        "招待费",
        "职工福利费",
        "办公费",
    ]


def test_summary_groups_ignore_legacy_currency_values_in_rmb_mode():
    invoice = document(description="打印纸采购", amount="88.50")
    payment = document(
        2,
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
        document_type=DocumentType.PAYMENT_SCREENSHOT,
        description="打印纸采购",
        amount="80.00",
    )
    foreign_payment = document(
        3,
        source_folder=SourceFolder.PAYMENT_EVIDENCE,
        document_type=DocumentType.PAYMENT_SCREENSHOT,
        description="打印纸采购",
        amount="8.50",
        currency="USD",
    )
    documents = [invoice, payment, foreign_payment]
    classifications = classify_expenses(documents)

    groups = build_expense_summary_groups(documents, classifications)
    assert len(groups) == 1
    group = groups[0]
    assert group.invoice_amount == Decimal("88.50")
    assert group.payment_amount == Decimal("88.50")
    assert group.difference == Decimal("0.00")
    assert group.reconciliation_status == "金额一致"


def test_missing_currency_is_not_a_classification_risk_in_rmb_mode():
    complete = document(description="打印纸采购", currency=None)
    classification = classify_expense_by_rule(complete)

    assert classification is not None
    assert classification_risk_reason(complete, object(), classification) == ""

    missing_amount = document(
        2,
        description="打印纸采购",
        amount=None,
        currency=None,
    )
    missing_classification = classify_expense_by_rule(missing_amount)

    assert missing_classification is not None
    assert classification_risk_reason(
        missing_amount,
        object(),
        missing_classification,
    ) == "金额缺失"
