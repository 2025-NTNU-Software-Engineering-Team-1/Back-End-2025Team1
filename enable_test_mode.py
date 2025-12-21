#!/usr/bin/env python3
"""
Enable Test Mode for existing trial test problem
"""
import sys

sys.path.insert(0, '/app')

from mongo import engine
from mongo.problem import Problem

# Find and update the existing problem
p = engine.Problem.objects(problem_name__contains='Trial Test').first()
if p:
    print(f'Found problem: {p.problem_id}')
    print(f'Current test_mode_enabled: {p.test_mode_enabled}')
    print(f'Current config: {p.config}')

    # Enable Test Mode - set the actual field
    p.test_mode_enabled = True

    # Also update config for consistency
    p.config = p.config or {}
    p.config['testMode'] = True
    p.config['testModeQuotaPerStudent'] = 100
    p.save()

    print(f'Updated test_mode_enabled: {p.test_mode_enabled}')
    print(f'Updated config: {p.config}')
    print('Test Mode enabled!')
else:
    print('No problem found')
