import hashlib
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from app.models.batch import SourceDocument
from app.models.review_reference import BatchReviewReference


MAX_REFERENCE_FILE_SIZE = 10 * 1024 * 1024
MAX_REFERENCE_ROWS = 2_000
MAX_REFERENCE_COLUMNS = 50
MAX_REFERENCE_CELL_CHARS = 500
MAX_REFERENCE_MATCHES = 3
MAX_REFERENCE_UNCOMPRESSED_SIZE = 100 * 1024 * 1024

REFERENCE_HEADER_ALIASES = {
    "expense_type": {"费用类型", "费用类别", "报销类型", "报销类别", "科目"},
    "expense_item": {"事项", "报销事项", "用途", "费用说明", "摘要"},
    "hospitality_target": {"招待对象", "接待对象", "招待客户", "客户名称"},
}


class ReviewReferenceValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedReviewReference:
    file_hash: str
    file_size: int
    sheet_names: list[str]
    rows: list[dict]


def normalize_reference_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _display_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    return str(value).strip()[:MAX_REFERENCE_CELL_CHARS]


def _header_mapping(rows: list[list[str]]) -> tuple[int | None, dict[int, str]]:
    for row_index, cells in enumerate(rows[:20]):
        mapping: dict[int, str] = {}
        for column_index, cell in enumerate(cells):
            normalized = normalize_reference_text(cell)
            for field, aliases in REFERENCE_HEADER_ALIASES.items():
                if normalized in {
                    normalize_reference_text(alias) for alias in aliases
                }:
                    mapping[column_index] = field
                    break
        if "expense_type" in mapping.values() or len(mapping) >= 2:
            return row_index, mapping
    return None, {}


def parse_review_reference(file_data: bytes, filename: str) -> ParsedReviewReference:
    if not filename.lower().endswith(".xlsx"):
        raise ReviewReferenceValidationError("仅支持 .xlsx 报销明细参考表")
    if not file_data:
        raise ReviewReferenceValidationError("上传的参考表为空")
    if len(file_data) > MAX_REFERENCE_FILE_SIZE:
        raise ReviewReferenceValidationError("参考表大小不能超过 10 MB")

    try:
        with zipfile.ZipFile(BytesIO(file_data)) as archive:
            members = archive.infolist()
            if any(member.flag_bits & 0x1 for member in members):
                raise ReviewReferenceValidationError("不支持加密的参考表")
            if any(member.filename.lower().endswith("vbaproject.bin") for member in members):
                raise ReviewReferenceValidationError("不支持包含宏的参考表")
            if sum(member.file_size for member in members) > MAX_REFERENCE_UNCOMPRESSED_SIZE:
                raise ReviewReferenceValidationError("参考表解压后内容超过安全限制")
    except ReviewReferenceValidationError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReviewReferenceValidationError("参考表不是有效的 .xlsx 文件") from exc

    try:
        workbook = load_workbook(
            BytesIO(file_data),
            read_only=True,
            data_only=True,
            keep_vba=False,
        )
    except Exception as exc:
        raise ReviewReferenceValidationError("参考表损坏、加密或不是有效的 .xlsx 文件") from exc

    rows: list[dict] = []
    sheet_names: list[str] = []
    try:
        for worksheet in workbook.worksheets:
            if worksheet.sheet_state != "visible":
                continue
            if worksheet.max_row > MAX_REFERENCE_ROWS:
                raise ReviewReferenceValidationError(
                    f"工作表“{worksheet.title}”超过 2,000 行限制"
                )
            if worksheet.max_column > MAX_REFERENCE_COLUMNS:
                raise ReviewReferenceValidationError(
                    f"工作表“{worksheet.title}”超过 50 列限制"
                )
            sheet_names.append(worksheet.title)
            worksheet_rows = [
                [_display_cell(value) for value in values]
                for values in worksheet.iter_rows(
                    max_col=MAX_REFERENCE_COLUMNS,
                    values_only=True,
                )
            ]
            header_index, header_mapping = _header_mapping(worksheet_rows)
            for row_index, row_cells in enumerate(worksheet_rows, start=1):
                non_empty_cells = [value for value in row_cells if value]
                if not non_empty_cells:
                    continue
                if len(rows) >= MAX_REFERENCE_ROWS:
                    raise ReviewReferenceValidationError("参考表有效行数不能超过 2,000 行")
                text = " | ".join(non_empty_cells)
                fields = {
                    field: row_cells[column_index]
                    for column_index, field in header_mapping.items()
                    if column_index < len(row_cells) and row_cells[column_index]
                }
                rows.append(
                    {
                        "sheet": worksheet.title,
                        "row": row_index,
                        "text": text,
                        "normalized": normalize_reference_text(text),
                        "cells": row_cells,
                        "fields": (
                            fields
                            if header_index is not None and row_index - 1 > header_index
                            else {}
                        ),
                    }
                )
    finally:
        workbook.close()

    if not rows:
        raise ReviewReferenceValidationError("参考表没有可用于审核的非空数据行")
    return ParsedReviewReference(
        file_hash=hashlib.sha256(file_data).hexdigest(),
        file_size=len(file_data),
        sheet_names=sheet_names,
        rows=rows,
    )


def _filename_stem(filename: str) -> str:
    return filename.rsplit(".", 1)[0] if "." in filename else filename


def _amount_tokens(document: SourceDocument) -> set[str]:
    if document.amount is None:
        return set()
    amount = Decimal(str(document.amount))
    tokens = {format(amount, ".2f"), format(amount, "f")}
    if amount == amount.to_integral():
        tokens.add(str(int(amount)))
    return {normalize_reference_text(token) for token in tokens if token}


def _date_tokens(document: SourceDocument) -> set[str]:
    if document.document_date is None:
        return set()
    value = document.document_date
    return {
        normalize_reference_text(value.isoformat()),
        normalize_reference_text(f"{value.year}年{value.month}月{value.day}日"),
    }


def match_reference_rows(
    document: SourceDocument,
    reference: BatchReviewReference | None,
) -> list[dict]:
    if reference is None:
        return []

    identifiers = {
        normalize_reference_text(document.invoice_number),
        normalize_reference_text(document.transaction_number),
    }
    identifiers = {value for value in identifiers if len(value) >= 6}
    filename = normalize_reference_text(_filename_stem(document.file_name))
    amount_tokens = _amount_tokens(document)
    date_tokens = _date_tokens(document)
    counterparty = normalize_reference_text(document.counterparty)

    matches: list[tuple[int, dict, str]] = []
    reference_rows = reference.parsed_rows or []
    if reference.file_data and any("fields" not in row for row in reference_rows):
        try:
            reference_rows = parse_review_reference(
                reference.file_data,
                reference.original_filename,
            ).rows
        except ReviewReferenceValidationError:
            pass

    for row in reference_rows:
        normalized = row.get("normalized") or normalize_reference_text(row.get("text"))
        reason = None
        priority = 0
        if identifiers and any(identifier in normalized for identifier in identifiers):
            priority, reason = 4, "发票号或交易单号匹配"
        elif len(filename) >= 4 and filename in normalized:
            priority, reason = 3, "文件名匹配"
        elif (
            amount_tokens
            and date_tokens
            and any(token in normalized for token in amount_tokens)
            and any(token in normalized for token in date_tokens)
        ):
            priority, reason = 2, "日期与金额匹配"
        elif (
            len(counterparty) >= 3
            and amount_tokens
            and counterparty in normalized
            and any(token in normalized for token in amount_tokens)
        ):
            priority, reason = 1, "对方与金额匹配"
        if reason:
            matches.append((priority, row, reason))

    matches.sort(key=lambda item: (-item[0], item[1].get("sheet", ""), item[1].get("row", 0)))
    return [
        {
            "sheet": row.get("sheet", ""),
            "row": row.get("row"),
            "text": str(row.get("text", ""))[:300],
            "match_reason": reason,
            "match_priority": priority,
            "fields": row.get("fields") or {},
        }
        for priority, row, reason in matches[:MAX_REFERENCE_MATCHES]
    ]


def format_reference_evidence(
    reference: BatchReviewReference | None,
    matches: list[dict],
) -> str:
    if reference is None or not matches:
        return ""
    parts = [
        f"{item['sheet']}!第{item['row']}行（{item['match_reason']}）：{item['text']}"
        for item in matches
    ]
    return f"辅助表v{reference.version}；" + "；".join(parts)
