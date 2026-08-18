"""Parse reimbursement claim workbooks and write the 系统审核 column."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet


AUDIT_COLUMN_TITLE = "系统审核"
MAX_HEADER_SCAN_ROWS = 20
MAX_CLAIM_ROWS = 2_000
MAX_CLAIM_COLUMNS = 50
EXCEL_EPOCH = date(1899, 12, 30)
PLACEHOLDER_INVOICE_MARKERS = ("必填", "数电发票号码", "请填写")
TOTAL_MARKERS = ("合计", "总计")

HEADER_ALIASES = {
    "claim_date": {"时间", "日期"},
    "applicant": {"申请人员", "申请人", "员工姓名", "姓名"},
    "expense_type": {"费用类型", "费用类别", "报销类型", "报销类别", "科目"},
    "item": {"事项", "报销事项", "用途", "费用说明", "摘要"},
    "amount": {"金额", "金额（元）", "金额(元)", "金额元"},
    "invoice_number": {"发票号码", "发票号"},
}


class ClaimWorkbookError(ValueError):
    pass


@dataclass(frozen=True)
class ClaimRow:
    sheet: str
    excel_row: int
    claim_date: date | None
    expense_month: date | None
    applicant: str
    expense_type: str
    item: str
    amount: Decimal | None
    invoice_number: str


@dataclass(frozen=True)
class ParsedClaimWorkbook:
    sheet_name: str
    header_row: int
    rows: list[ClaimRow]


def normalize_claim_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", "", text)


def _header_key(value: object) -> str:
    return normalize_claim_text(value).casefold()


def _display(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    return str(value).strip()


def _parse_amount(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except InvalidOperation:
            return None
    text = str(value).strip().replace(",", "")
    text = re.sub(r"[元￥¥]", "", text)
    if not text:
        return None
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _parse_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and value > 20000:
        try:
            return EXCEL_EPOCH + timedelta(days=int(value))
        except OverflowError:
            return None
    text = str(value).strip()
    for pattern, parser in (
        (r"^(\d{4})-(\d{1,2})-(\d{1,2})$", None),
        (r"^(\d{4})/(\d{1,2})/(\d{1,2})$", None),
        (r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$", None),
    ):
        match = re.fullmatch(pattern, text)
        if match:
            year, month, day = (int(part) for part in match.groups())
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def _clean_invoice_number(value: object) -> str:
    text = _display(value)
    if not text:
        return ""
    if any(marker in text for marker in PLACEHOLDER_INVOICE_MARKERS):
        return ""
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return digits
    return ""


def _is_total_row(item: str, cells: list[object]) -> bool:
    joined = "".join(_display(cell) for cell in cells)
    if any(marker in item for marker in TOTAL_MARKERS):
        return True
    return any(marker in joined and not item for marker in TOTAL_MARKERS)


def _find_header(rows: list[list[Any]]) -> tuple[int, dict[int, str]] | None:
    for index, cells in enumerate(rows[:MAX_HEADER_SCAN_ROWS]):
        mapping: dict[int, str] = {}
        for column_index, cell in enumerate(cells):
            header = _header_key(cell)
            for field, aliases in HEADER_ALIASES.items():
                if header in {_header_key(alias) for alias in aliases}:
                    mapping[column_index] = field
                    break
        mapped = set(mapping.values())
        if {"claim_date", "item", "amount"} <= mapped:
            return index, mapping
    return None


def parse_claim_workbook(file_data: bytes) -> ParsedClaimWorkbook:
    if not file_data:
        raise ClaimWorkbookError("报销总表为空")
    try:
        workbook = load_workbook(BytesIO(file_data), data_only=True, read_only=True)
    except Exception as exc:
        raise ClaimWorkbookError("报销总表不是有效的 .xlsx 文件") from exc

    try:
        for worksheet in workbook.worksheets:
            if worksheet.sheet_state != "visible":
                continue
            sheet_rows = [
                list(values)
                for values in worksheet.iter_rows(
                    max_col=MAX_CLAIM_COLUMNS,
                    values_only=True,
                )
            ]
            header = _find_header(sheet_rows)
            if header is None:
                continue
            header_index, mapping = header
            claims: list[ClaimRow] = []
            for offset, cells in enumerate(sheet_rows[header_index + 1 :], start=1):
                if len(claims) >= MAX_CLAIM_ROWS:
                    raise ClaimWorkbookError("报销总表有效行数不能超过 2,000 行")
                fields = {
                    field: cells[column_index] if column_index < len(cells) else None
                    for column_index, field in mapping.items()
                }
                item = _display(fields.get("item"))
                amount = _parse_amount(fields.get("amount"))
                claim_date = _parse_date(fields.get("claim_date"))
                applicant = _display(fields.get("applicant"))
                expense_type = _display(fields.get("expense_type"))
                if not item and amount is None and claim_date is None:
                    continue
                if _is_total_row(item, cells):
                    continue
                if not item or amount is None:
                    continue
                expense_month = (
                    date(claim_date.year, claim_date.month, 1) if claim_date else None
                )
                claims.append(
                    ClaimRow(
                        sheet=worksheet.title,
                        excel_row=header_index + 1 + offset,
                        claim_date=claim_date,
                        expense_month=expense_month,
                        applicant=applicant,
                        expense_type=expense_type,
                        item=item,
                        amount=amount,
                        invoice_number=_clean_invoice_number(
                            fields.get("invoice_number")
                        ),
                    )
                )
            if not claims:
                raise ClaimWorkbookError("报销总表没有可用于审核的明细行")
            return ParsedClaimWorkbook(
                sheet_name=worksheet.title,
                header_row=header_index + 1,
                rows=claims,
            )
    finally:
        workbook.close()

    raise ClaimWorkbookError("报销总表未找到包含时间、事项和金额的工作表")


def claims_to_json(rows: list[ClaimRow]) -> list[dict[str, Any]]:
    payload = []
    for row in rows:
        payload.append(
            {
                "sheet": row.sheet,
                "excel_row": row.excel_row,
                "claim_date": row.claim_date.isoformat() if row.claim_date else None,
                "expense_month": (
                    row.expense_month.isoformat() if row.expense_month else None
                ),
                "applicant": row.applicant,
                "expense_type": row.expense_type,
                "item": row.item,
                "amount": str(row.amount) if row.amount is not None else None,
                "invoice_number": row.invoice_number,
            }
        )
    return payload


def claims_from_json(rows: list[dict[str, Any]] | None) -> list[ClaimRow]:
    result: list[ClaimRow] = []
    for row in rows or []:
        claim_date = _parse_date(row.get("claim_date"))
        expense_month = _parse_date(row.get("expense_month"))
        result.append(
            ClaimRow(
                sheet=str(row.get("sheet") or ""),
                excel_row=int(row.get("excel_row") or 0),
                claim_date=claim_date,
                expense_month=expense_month,
                applicant=str(row.get("applicant") or ""),
                expense_type=str(row.get("expense_type") or ""),
                item=str(row.get("item") or ""),
                amount=_parse_amount(row.get("amount")),
                invoice_number=str(row.get("invoice_number") or ""),
            )
        )
    return result


def _header_cells(worksheet: Worksheet, max_row: int = MAX_HEADER_SCAN_ROWS) -> tuple[int, list[Any]]:
    for row_index in range(1, max_row + 1):
        values = [
            worksheet.cell(row_index, column_index).value
            for column_index in range(1, MAX_CLAIM_COLUMNS + 1)
        ]
        mapping_rows = [values]
        header = _find_header(mapping_rows)
        if header is not None:
            return row_index, values
    raise ClaimWorkbookError("无法在原表中定位表头")


def _audit_column_index(header_values: list[Any]) -> int:
    for index, value in enumerate(header_values, start=1):
        if _header_key(value) == _header_key(AUDIT_COLUMN_TITLE):
            return index
    last_used = 0
    for index, value in enumerate(header_values, start=1):
        if value not in (None, ""):
            last_used = index
    return last_used + 1


def annotate_claim_workbook(
    file_data: bytes,
    *,
    sheet_name: str,
    remarks_by_row: dict[int, str],
) -> bytes:
    workbook = load_workbook(BytesIO(file_data))
    if sheet_name not in workbook.sheetnames:
        raise ClaimWorkbookError(f"原表缺少工作表“{sheet_name}”")
    worksheet = workbook[sheet_name]
    header_row, header_values = _header_cells(worksheet)
    column_index = _audit_column_index(header_values)
    header_cell = worksheet.cell(header_row, column_index)
    header_cell.value = AUDIT_COLUMN_TITLE
    for excel_row, remark in remarks_by_row.items():
        worksheet.cell(excel_row, column_index).value = remark
    worksheet.column_dimensions[get_column_letter(column_index)].width = 28
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
