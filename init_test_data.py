#!/usr/bin/env python3
"""
初始化測試數據

此腳本在 web 容器內運行，創建測試用戶和題目。
"""

import sys

sys.path.insert(0, '/app')

import hashlib
from mongo import engine
from mongo.user import User, hash_id


def create_test_users():
    """創建測試用戶"""
    users_to_create = [
        {
            "username": "admin",
            "password": "admin",
            "email": "admin@test.com",
            "role": 0
        },
        {
            "username": "teacher",
            "password": "teacher",
            "email": "teacher@test.com",
            "role": 1
        },
        {
            "username": "student",
            "password": "student",
            "email": "student@test.com",
            "role": 2
        },
    ]

    created = []
    for user_data in users_to_create:
        try:
            # Check if user exists
            existing = engine.User.objects(
                username=user_data["username"]).first()
            if existing:
                print(f"用戶 {user_data['username']} 已存在，跳過")
                continue

            # Create user
            user_id = hash_id(user_data["username"], user_data["password"])
            email = user_data["email"].lower().strip()

            user = engine.User(
                user_id=user_id,
                user_id2=user_id,
                username=user_data["username"],
                email=email,
                md5=hashlib.md5(email.encode()).hexdigest(),
                active=True,  # 直接啟用
                role=user_data["role"],
            ).save(force_insert=True)

            print(f"創建用戶: {user_data['username']} (role={user_data['role']})")
            created.append(user_data["username"])

        except Exception as e:
            print(f"創建用戶 {user_data['username']} 失敗: {e}")

    return created


def create_test_course():
    """創建測試課程"""
    try:
        from mongo.course import Course

        # Check if test course exists
        existing = engine.Course.objects(course_name="TestCourse").first()
        if existing:
            print(f"測試課程已存在: TestCourse")
            return "TestCourse"

        # Create course
        success = Course.add_course("TestCourse", "admin")
        if success:
            print("創建測試課程: TestCourse")
            return "TestCourse"
    except Exception as e:
        print(f"創建測試課程失敗: {e}")
        import traceback
        traceback.print_exc()
    return None


def create_test_problem():
    """創建測試題目"""
    try:
        from mongo.problem import Problem

        # Check if test problem exists
        existing = engine.Problem.objects(
            problem_name__contains="Trial Test").first()
        if existing:
            print(f"測試題目已存在: {existing.problem_id}")
            return existing.problem_id

        # Get admin user
        admin_user = User("admin")

        # Ensure course exists
        course_name = create_test_course()
        if not course_name:
            print("無法創建課程，使用空課程列表")
            course_name = None

        courses = [course_name] if course_name else []

        # Create problem
        problem = Problem.add(
            user=admin_user,
            problem_name="Trial Test Problem",
            description={
                "description":
                "A simple addition problem for trial submission testing.\n\nInput: Two integers a and b\nOutput: a + b",
                "input": "Two integers a and b, space-separated",
                "output": "The sum of a and b",
                "hint": "",
                "sampleInput": ["3 5"],
                "sampleOutput": ["8"],
            },
            courses=courses,
            status=0,  # Online
            tags=[],
            type=0,  # Normal
            allowed_language=7,  # All languages (C, C++, Python)
            quota=-1,
            test_case_info={
                "language":
                2,  # Python
                "fill_in_template":
                "",
                "tasks": [{
                    "caseCount": 1,
                    "taskScore": 100,
                    "memoryLimit": 65536,
                    "timeLimit": 3000,
                }]
            },
        )

        if problem is not None:
            # Problem.add() returns problem_id directly (int)
            print(f"創建測試題目: {problem}")
            return problem
    except Exception as e:
        print(f"創建測試題目失敗: {e}")
        import traceback
        traceback.print_exc()

    return None


def main():
    print("=" * 50)
    print("初始化測試數據")
    print("=" * 50)

    # 連接數據庫
    try:
        from mongo import config
        print(f"MongoDB: {config.MONGO_HOST}")
    except Exception as e:
        print(f"配置錯誤: {e}")

    # 創建用戶
    print("\n--- 創建測試用戶 ---")
    users = create_test_users()

    # 創建題目
    print("\n--- 創建測試題目 ---")
    problem_id = create_test_problem()

    print("\n" + "=" * 50)
    print("完成！")
    print("=" * 50)

    if users:
        print(f"\n創建的用戶: {', '.join(users)}")
    if problem_id:
        print(f"測試題目 ID: {problem_id}")

    print("\n可用的測試帳號:")
    print("  admin:admin (管理員)")
    print("  teacher:teacher (教師)")
    print("  student:student (學生)")


if __name__ == "__main__":
    main()
