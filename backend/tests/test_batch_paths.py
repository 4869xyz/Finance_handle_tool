from io import BytesIO

import pytest
from fastapi import UploadFile

from app.models.batch import SourceFolder
from app.services.batch_service import BatchValidationError, validate_upload_paths


def upload(name: str) -> UploadFile:
    return UploadFile(file=BytesIO(b"x"), filename=name)


def test_paths_support_direct_and_one_opaque_root():
    direct = validate_upload_paths(
        [upload("a.pdf"), upload("b.jpg")],
        ["发票/a.pdf", "支付凭证/项目/b.jpg"],
    )
    assert direct[0].source_folder == SourceFolder.INVOICE
    assert direct[1].relative_path == "支付凭证/项目/b.jpg"

    rooted = validate_upload_paths(
        [upload("a.pdf"), upload("b.jpg")],
        ["任意员工月份目录/发票/a.pdf", "任意员工月份目录/补票/子目录/b.jpg"],
    )
    assert rooted[0].relative_path == "发票/a.pdf"
    assert rooted[1].source_folder == SourceFolder.REPLACEMENT_INVOICE


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/发票/a.pdf", "绝对路径"),
        ("C:/发票/a.pdf", "绝对路径"),
        ("根目录/其他/a.pdf", "必须位于"),
        ("根目录/发票/../a.pdf", "非法层级"),
        ("根目录/发票/a.exe", "不支持"),
    ],
)
def test_invalid_paths_are_rejected(path, message):
    with pytest.raises(BatchValidationError, match=message):
        validate_upload_paths([upload(path.split("/")[-1])], [path])


def test_mixed_roots_and_file_path_count_mismatch_are_rejected():
    with pytest.raises(BatchValidationError, match="一致的根目录模式"):
        validate_upload_paths(
            [upload("a.pdf"), upload("b.pdf")],
            ["根一/发票/a.pdf", "根二/发票/b.pdf"],
        )

    with pytest.raises(BatchValidationError, match="数量必须完全一致"):
        validate_upload_paths([upload("a.pdf")], [])

    with pytest.raises(BatchValidationError, match="文件名与相对路径不匹配"):
        validate_upload_paths([upload("a.pdf")], ["发票/b.pdf"])

