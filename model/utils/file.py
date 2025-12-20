from flask import Response, stream_with_context
import tempfile
import zipfile

__all__ = ['stream_zip_response']


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
