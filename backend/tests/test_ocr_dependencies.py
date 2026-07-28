import importlib.metadata as metadata

import cv2


def test_ocr_dependency_set_is_locked_and_has_no_headless_opencv():
    assert metadata.version("paddlepaddle") == "3.0.0"
    assert metadata.version("paddleocr") == "2.7.3"
    assert metadata.version("numpy") == "1.26.4"
    assert metadata.version("pdf2docx") == "0.5.7"
    assert metadata.version("opencv-python") == "4.6.0.66"
    assert metadata.version("opencv-contrib-python") == "4.6.0.66"

    installed_opencv_packages = {
        distribution.metadata["Name"].lower()
        for distribution in metadata.distributions()
        if (distribution.metadata["Name"] or "").lower().startswith("opencv")
    }
    assert installed_opencv_packages == {
        "opencv-python",
        "opencv-contrib-python",
    }
    assert cv2.__version__.startswith("4.6.0")
    assert hasattr(cv2, "ximgproc")
