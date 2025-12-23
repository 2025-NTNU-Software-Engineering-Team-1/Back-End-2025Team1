#!/usr/bin/env python3
"""
Script to check trial_mode_enabled status for a problem in the database.
Usage: python check_trial_mode.py <problem_id>
"""
import sys
import os

# Add the Back-End directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mongo import engine
from mongo.problem import Problem


def check_trial_mode(problem_id: int):
    """Check trial_mode_enabled status for a problem."""
    try:
        # Load problem from database
        problem_obj = engine.Problem.objects(problem_id=problem_id).first()

        if not problem_obj:
            print(f"❌ Problem {problem_id} not found in database")
            return

        # Check trial_mode_enabled
        trial_mode_enabled = getattr(problem_obj, 'trial_mode_enabled', None)

        print(f"📋 Problem Information:")
        print(f"   Problem ID: {problem_id}")
        print(
            f"   Problem Name: {getattr(problem_obj, 'problem_name', 'N/A')}")
        print(f"   Trial Mode Enabled: {trial_mode_enabled}")

        # Also check using Problem wrapper
        problem = Problem(problem_id)
        if problem and problem.obj:
            trial_mode_from_wrapper = getattr(problem.obj,
                                              'trial_mode_enabled', None)
            print(
                f"   Trial Mode (via Problem wrapper): {trial_mode_from_wrapper}"
            )

            # Check all related fields
            print(f"\n📊 Related Fields:")
            print(
                f"   trial_mode_enabled (direct): {getattr(problem.obj, 'trial_mode_enabled', 'N/A')}"
            )
            print(
                f"   trialModeEnabled (DB field): {getattr(problem.obj, 'trialModeEnabled', 'N/A')}"
            )

            # Check if field exists in document
            if hasattr(problem.obj, '_data'):
                print(
                    f"   In _data: {problem.obj._data.get('trialModeEnabled', 'Not in _data')}"
                )
        else:
            print(f"   ⚠️  Problem wrapper returned None")

    except Exception as e:
        print(f"❌ Error checking problem: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python check_trial_mode.py <problem_id>")
        sys.exit(1)

    try:
        problem_id = int(sys.argv[1])
        check_trial_mode(problem_id)
    except ValueError:
        print("❌ Problem ID must be an integer")
        sys.exit(1)
