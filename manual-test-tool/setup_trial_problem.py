#!/usr/bin/env python3
"""
Set up the trial test problem with public test cases and AC code
"""
import sys

sys.path.insert(0, '/app')

import io
import zipfile
from mongo import engine
from mongo.utils import MinioClient


def create_public_testcases_zip():
    """Create a zip with public test cases"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Test case 1: 3 + 5 = 8
        zf.writestr("0000.in", "3 5\n")
        zf.writestr("0000.out", "8\n")
        # Test case 2: 10 + 20 = 30
        zf.writestr("0001.in", "10 20\n")
        zf.writestr("0001.out", "30\n")
    buffer.seek(0)
    return buffer.getvalue()


def create_ac_code_zip():
    """Create a zip with AC solution code"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # AC solution in Python
        code = """# AC Solution
a, b = map(int, input().split())
print(a + b)
"""
        zf.writestr("main.py", code)
    buffer.seek(0)
    return buffer.getvalue()


def main():
    print("=" * 50)
    print("設置 Trial Test Problem")
    print("=" * 50)

    # Find the problem
    p = engine.Problem.objects(problem_name__contains='Trial Test').first()
    if not p:
        print("找不到 Trial Test Problem!")
        return

    print(f"找到題目: {p.problem_id} - {p.problem_name}")

    # Create MinIO client
    minio = MinioClient()

    # 1. Upload public test cases
    print("\n--- 上傳公開測試案例 ---")
    testcases_data = create_public_testcases_zip()
    testcases_path = f"problem/{p.problem_id}/public_testcases.zip"

    try:
        minio.client.put_object(minio.bucket,
                                testcases_path,
                                io.BytesIO(testcases_data),
                                length=len(testcases_data))
        p.public_cases_zip_minio_path = testcases_path
        print(f"  上傳成功: {testcases_path}")
    except Exception as e:
        print(f"  上傳失敗: {e}")

    # 2. Upload AC code
    print("\n--- 上傳 AC 代碼 ---")
    ac_code_data = create_ac_code_zip()
    ac_code_path = f"problem/{p.problem_id}/ac_code.zip"

    try:
        minio.client.put_object(minio.bucket,
                                ac_code_path,
                                io.BytesIO(ac_code_data),
                                length=len(ac_code_data))
        p.ac_code_minio_path = ac_code_path
        p.ac_code_language = 2  # Python
        print(f"  上傳成功: {ac_code_path}")
    except Exception as e:
        print(f"  上傳失敗: {e}")

    # Save changes
    p.save()

    print("\n--- 驗證 ---")
    print(f"  public_cases_zip_minio_path: {p.public_cases_zip_minio_path}")
    print(f"  ac_code_minio_path: {p.ac_code_minio_path}")
    print(f"  ac_code_language: {p.ac_code_language}")
    print(f"  test_mode_enabled: {p.test_mode_enabled}")

    print("\n" + "=" * 50)
    print("設置完成！")
    print("=" * 50)


if __name__ == "__main__":
    main()
