# -*- coding: utf-8 -*-
"""
电脑医生 AI 交互式测试工具
用法: python chat.py
输入问题即可测试，输入 quit 退出
"""
import sys, os, io, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ai_engine import AIEngine

engine = AIEngine()

print("=" * 60)
print("  电脑医生 AI 交互式测试")
print("  输入问题即可测试，输入 quit 退出")
print("  输入 verbose 切换详细模式")
print("=" * 60)
print()

verbose = False

while True:
    try:
        q = input("\n>>> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n再见！")
        break

    if not q:
        continue
    if q.lower() == 'quit':
        print("再见！")
        break
    if q.lower() == 'verbose':
        verbose = not verbose
        print(f"[详细模式: {'开启' if verbose else '关闭'}]")
        continue

    start = time.time()
    result = engine.ask(q)
    elapsed = time.time() - start

    try:
        conf = float(result.get('confidence', 0))
    except (ValueError, TypeError):
        conf = 0.0

    print()
    print(f"模式: {result.get('mode', '?')} | 层级: {result.get('layer', '?')} | 耗时: {elapsed:.2f}s")
    print(f"置信度: {conf:.0%}")
    print("-" * 40)
    print(result.get('answer', '(无答案)'))
    print("-" * 40)

    if verbose:
        print(f"\n[调试] 完整结果:")
        for k, v in result.items():
            print(f"  {k}: {v}")
