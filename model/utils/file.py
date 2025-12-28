from flask import Response, stream_with_context
import tempfile
import zipfile
import io
from typing import Tuple, Optional

__all__ = ['stream_zip_response', 'zip_sanitize', 'macos_zip_sanitize']


def macos_zip_sanitize(zip_bytes: bytes) -> Tuple[bool, Optional[str]]:
    """
    檢查 zip 是否包含 macOS 特徵檔案。

    macOS 使用 Finder 壓縮時會產生：
    - __MACOSX/ 資料夾
    - ._ 開頭的 AppleDouble 檔案（資源分支）
    - .DS_Store 檔案

    Args:
        zip_bytes: zip 檔案的位元組內容

    Returns:
        (has_macos_files, error_message)
        - has_macos_files: True 表示包含 macOS 檔案
        - error_message: 檢測到的問題描述
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                # 檢查 __MACOSX 資料夾
                if name.startswith('__MACOSX/') or name == '__MACOSX':
                    return (
                        True, 'Zip contains macOS metadata (__MACOSX folder). '
                        'Please use a cross-platform archiver or remove macOS files.'
                    )

                # 檢查 ._ 開頭的 AppleDouble 檔案
                basename = name.rsplit('/', 1)[-1]
                if basename.startswith('._'):
                    return (True,
                            f'Zip contains macOS AppleDouble file: {name}. '
                            'Please use a cross-platform archiver.')

                # 檢查 .DS_Store
                if basename == '.DS_Store':
                    return (True, 'Zip contains macOS .DS_Store file. '
                            'Please use a cross-platform archiver.')

    except zipfile.BadZipFile:
        # 不在此處處理壞 zip，讓呼叫方處理
        pass

    return (False, None)


def zip_sanitize(zip_bytes: bytes) -> Tuple[bool, Optional[str]]:
    """
    綜合檢查 zip 檔案是否符合上傳規範。

    目前包含的檢查：
    - macOS 特徵檔案檢測

    Args:
        zip_bytes: zip 檔案的位元組內容

    Returns:
        (is_valid, error_message)
        - is_valid: True 表示通過所有檢查
        - error_message: 失敗時的錯誤訊息
    """
    # macOS 檢查
    has_macos, macos_error = macos_zip_sanitize(zip_bytes)
    if has_macos:
        return (False, macos_error)

    return (True, None)


def stream_zip_response(files_iterator, attachment_filename):
    """
    Helper function to stream a zip file creation.
    
    Args:
        files_iterator: A generator yielding (filename_in_zip, file_bytes_content)
        attachment_filename: The filename for the browser download
    """

    def generate():
        # 使用 SpooledTemporaryFile，超過 10MB 會自動轉存到硬碟
        with tempfile.SpooledTemporaryFile(max_size=10 * 1024 *
                                           1024) as tmp_file:
            with zipfile.ZipFile(tmp_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                # 迭代外部傳入的檔案資料
                for fname, data in files_iterator():
                    if data:
                        zf.writestr(fname, data)
                        # 顯式刪除變數提示 GC 回收
                        del data

            # 寫入完成，指針移回開頭準備讀取
            tmp_file.seek(0)

            # 分塊傳輸
            while True:
                chunk = tmp_file.read(8192)  # 每次讀 8KB
                if not chunk:
                    break
                yield chunk

    headers = {
        'Content-Disposition': f'attachment; filename={attachment_filename}',
        'Content-Type': 'application/zip'
    }

    return Response(stream_with_context(generate()), headers=headers)
