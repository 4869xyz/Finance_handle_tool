"""Split a reimbursement package folder into monthly batches plus one claim workbook."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.reimbursement_package import PackageStatus, ReimbursementPackage
from app.services.batch_service import (
    SOURCE_FOLDER_NAMES,
    VIDEO_EXTENSIONS,
    BatchValidationError,
    PreparedDocument,
    ValidatedUploadPath,
    _read_upload,
    register_batch_from_payloads,
)
from app.services.claim_workbook_service import (
    ClaimWorkbookError,
    claims_to_json,
    parse_claim_workbook,
)


MONTH_FOLDER_PATTERNS = (
    re.compile(r"^(.+?)(\d{4})年(\d{1,2})月报销发票$"),
    re.compile(r"^(.+?)-(\d{4})年(\d{1,2})月-报销$"),
)
WORKBOOK_SUFFIX = "报销总表.xlsx"


@dataclass(frozen=True)
class MonthFolder:
    employee_name: str
    expense_month: date
    folder_name: str


def _normalized_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def parse_month_folder_name(name: str) -> MonthFolder | None:
    normalized = _normalized_name(name).strip()
    for pattern in MONTH_FOLDER_PATTERNS:
        match = pattern.fullmatch(normalized)
        if not match:
            continue
        employee, year, month = match.group(1), int(match.group(2)), int(match.group(3))
        employee = employee.strip("-_ ")
        if not employee or month < 1 or month > 12:
            return None
        return MonthFolder(
            employee_name=employee,
            expense_month=date(year, month, 1),
            folder_name=normalized,
        )
    return None


def _split_parts(raw_path: str) -> list[str]:
    normalized = _normalized_name(raw_path.replace("\\", "/").strip())
    if not normalized or "\x00" in normalized:
        raise BatchValidationError("相对路径为空或包含非法字符")
    parts = [part for part in normalized.split("/") if part]
    if any(part in {".", ".."} or ":" in part for part in parts):
        raise BatchValidationError(f"路径包含非法层级：{raw_path}")
    return parts


def classify_package_path(raw_path: str) -> tuple[str, MonthFolder | None, ValidatedUploadPath | None]:
    parts = _split_parts(raw_path)
    file_name = parts[-1]
    lowered = file_name.casefold()

    if lowered.endswith(WORKBOOK_SUFFIX.casefold()) or file_name.endswith(WORKBOOK_SUFFIX):
        return "workbook", None, None
    if lowered == "报销使用事项.txt" or lowered.endswith(".txt"):
        return "skip", None, None
    extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if extension in {"xlsx", "xls", "xlsm"}:
        return "skip", None, None

    month_index = None
    month_folder = None
    for index, part in enumerate(parts[:-1]):
        parsed = parse_month_folder_name(part)
        if parsed is not None:
            month_index = index
            month_folder = parsed
            break
    if month_folder is None or month_index is None:
        raise BatchValidationError(
            f"文件必须位于月度报销文件夹的发票、补票或支付凭证目录下：{raw_path}"
        )

    rest = parts[month_index + 1 :]
    if not rest or rest[0] not in SOURCE_FOLDER_NAMES:
        raise BatchValidationError(
            f"路径必须位于发票、补票或支付凭证目录下：{raw_path}"
        )
    if len(rest) < 2:
        raise BatchValidationError(f"来源目录下缺少文件名：{raw_path}")
    stored_path = "/".join(rest)
    file_type = rest[-1].rsplit(".", 1)[-1].lower()
    settings = get_settings()
    if file_type not in settings.batch_allowed_extensions:
        raise BatchValidationError(f"不支持的文件类型：{file_type}")
    validated = ValidatedUploadPath(
        source_folder=SOURCE_FOLDER_NAMES[rest[0]],
        relative_path=stored_path,
        file_name=rest[-1],
        file_type=file_type,
        mode="rooted",
        opaque_root=month_folder.folder_name,
    )
    return "document", month_folder, validated


async def register_package(
    db: AsyncSession,
    *,
    files: list[UploadFile],
    relative_paths: list[str],
) -> ReimbursementPackage:
    settings = get_settings()
    if not files:
        raise BatchValidationError("至少需要上传一个文件")
    if len(files) != len(relative_paths):
        raise BatchValidationError("files 与 relative_paths 数量必须完全一致")
    if len(files) > settings.package_max_files:
        raise BatchValidationError(
            f"提交包最多允许 {settings.package_max_files} 个文件"
        )

    workbook_items: list[tuple[UploadFile, str]] = []
    month_files: dict[tuple[str, date], list[tuple[UploadFile, ValidatedUploadPath]]] = defaultdict(list)
    employees: set[str] = set()

    try:
        for upload, raw_path in zip(files, relative_paths, strict=True):
            kind, month_folder, validated = classify_package_path(raw_path)
            upload_name = _normalized_name(
                (upload.filename or "").replace("\\", "/").split("/")[-1]
            )
            path_name = _normalized_name(raw_path.replace("\\", "/").split("/")[-1])
            if not upload_name or upload_name != path_name:
                raise BatchValidationError(
                    f"文件名与相对路径不匹配：{upload.filename or 'unknown'}"
                )
            if kind == "workbook":
                workbook_items.append((upload, path_name))
                continue
            if kind == "skip":
                continue
            if month_folder is None or validated is None:
                continue
            employees.add(month_folder.employee_name)
            month_files[(month_folder.employee_name, month_folder.expense_month)].append(
                (upload, validated)
            )

        if len(workbook_items) != 1:
            raise BatchValidationError("提交包必须包含且仅包含一份报销总表.xlsx")
        if not month_files:
            raise BatchValidationError("提交包中没有可识别的月度发票或支付凭证文件")
        if len(employees) != 1:
            raise BatchValidationError("提交包内月度文件夹的员工姓名必须一致")

        employee_name = next(iter(employees))
        workbook_upload, workbook_name = workbook_items[0]
        workbook_bytes, workbook_size, workbook_hash = await _read_upload(
            workbook_upload,
            keep_content=True,
            total_before=0,
            max_total_size=settings.package_max_total_size,
        )
        if not workbook_bytes:
            raise BatchValidationError("报销总表为空")
        try:
            parsed = parse_claim_workbook(workbook_bytes)
        except ClaimWorkbookError as exc:
            raise BatchValidationError(str(exc)) from exc

        package = ReimbursementPackage(
            employee_name=employee_name,
            status=PackageStatus.PROCESSING,
            original_filename=workbook_name,
            file_hash=workbook_hash,
            file_size=workbook_size,
            file_data=workbook_bytes,
            sheet_name=parsed.sheet_name,
            parsed_claims=claims_to_json(parsed.rows),
        )
        db.add(package)
        await db.flush()

        package_total = workbook_size
        for (month_employee, expense_month), items in sorted(
            month_files.items(),
            key=lambda item: item[0][1],
        ):
            if len(items) > settings.batch_max_files:
                raise BatchValidationError(
                    f"{expense_month.strftime('%Y-%m')} 超过单月 {settings.batch_max_files} 个文件限制"
                )
            payloads: list[PreparedDocument] = []
            month_total = 0
            seen_paths: set[str] = set()
            for upload, path in items:
                path_key = path.relative_path.casefold()
                if path_key in seen_paths:
                    raise BatchValidationError(
                        f"批次内存在重复路径：{path.relative_path}"
                    )
                seen_paths.add(path_key)
                keep_content = path.file_type not in VIDEO_EXTENSIONS
                content, file_size, file_hash = await _read_upload(
                    upload,
                    keep_content=keep_content,
                    total_before=package_total,
                    max_total_size=settings.package_max_total_size,
                )
                if month_total + file_size > settings.batch_max_total_size:
                    raise BatchValidationError(
                        f"{expense_month.strftime('%Y-%m')} 超过单月 "
                        f"{settings.batch_max_total_size // 1024 // 1024} MiB 限制"
                    )
                month_total += file_size
                package_total += file_size
                payloads.append(
                    PreparedDocument(
                        path=path,
                        content=content,
                        file_size=file_size,
                        file_hash=file_hash,
                    )
                )
            await register_batch_from_payloads(
                db,
                employee_name=month_employee,
                expense_month=expense_month,
                payloads=payloads,
                package_id=package.id,
                commit=False,
            )

        await db.commit()
        await db.refresh(package)
        return package
    except Exception:
        await db.rollback()
        raise
    finally:
        for upload in files:
            await upload.close()
