#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vocab_export.py - 词汇校验与导出工具 (已整合至 memo_import.py)
作为向后兼容包装层，直接调用 memo_import.py 的校验功能。
"""

import sys
import os

# 确保能直接导入同目录的 memo_import
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from memo_import import main as memo_main

if __name__ == "__main__":
    # 若未显式传入 --validate-only 或 --dry-run，默认开启 --validate-only
    if "--validate-only" not in sys.argv and "--dry-run" not in sys.argv:
        sys.argv.insert(1, "--validate-only")
    memo_main()

