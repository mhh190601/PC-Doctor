# -*- coding: utf-8 -*-
"""
电脑医生 AI 引擎自动化测试脚本
================================
验证混合检索（精确匹配 + 标签匹配 + 语义检索 + 关键词兜底）的召回率。

用法:
    python test_ai.py                 # 静默模式，只输出汇总
    python test_ai.py --verbose       # 详细输出每条用例
    python test_ai.py --export report.json   # 导出 JSON 报告
    python test_ai.py --exit-on-fail  # CI 模式：召回率不达标则非零退出

召回率目标: >= 85%
"""
import argparse
import json
import os
import sys
import time

# ---- 修复 Windows GBK 控制台编码问题 ----
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 将脚本所在目录加入 path，确保能 import ai_engine
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ai_engine import get_engine


# ============================================================
# 测试集：至少 50 个常见问题 + 期望命中关键词
# 每条: (问题, 期望在答案中出现的关键词(任一同即可), 类别)
# ============================================================
TEST_CASES = [
    # --- 错误码精确匹配 ---
    ("蓝屏代码 0x000000EF 怎么解决", ["0x000000EF", "EF", "驱动", "终止"], "错误码"),
    ("0x0000007E 蓝屏", ["0x0000007E", "7E", "驱动", "不兼容"], "错误码"),
    ("0x00000050 错误", ["0x00000050", "50", "内存", "损坏"], "错误码"),
    ("0x0000001A 蓝屏", ["0x0000001A", "1A", "内存", "故障"], "错误码"),
    ("0x000000D1 怎么办", ["0x000000D1", "D1", "驱动", "冲突"], "错误码"),
    ("0x00000024 蓝屏", ["0x00000024", "24", "NTFS", "硬盘"], "错误码"),
    ("0xC000021A 错误", ["0xC000021A", "21A", "系统", "会话"], "错误码"),
    ("0x0000009F 蓝屏", ["0x0000009F", "9F", "电源", "驱动"], "错误码"),
    ("0x000000A5 怎么解决", ["0x000000A5", "A5", "ACPI", "BIOS"], "错误码"),
    ("0x0000007B 蓝屏", ["0x0000007B", "7B", "硬盘", "模式"], "错误码"),

    # --- 蓝屏/黑屏/花屏 ---
    ("电脑蓝屏了怎么解决", ["蓝屏", "重启", "驱动", "内存", "错误"], "蓝屏"),
    ("频繁蓝屏是什么原因", ["蓝屏", "驱动", "内存", "硬件", "温度"], "蓝屏"),
    ("黑屏只有鼠标箭头", ["黑屏", "鼠标", "资源管理器", "explorer"], "黑屏"),
    ("开机黑屏怎么办", ["黑屏", "显卡", "内存", "显示", "连接"], "黑屏"),
    ("屏幕花屏怎么解决", ["花屏", "显卡", "驱动", "过热", "显存"], "花屏"),

    # --- 卡顿/死机 ---
    ("电脑特别卡怎么办", ["卡", "清理", "启动项", "内存", "磁盘"], "卡顿"),
    ("电脑运行很慢怎么优化", ["慢", "优化", "启动项", "清理", "内存"], "卡顿"),
    ("电脑经常死机", ["死机", "温度", "内存", "散热", "蓝屏"], "卡顿"),
    ("玩游戏卡顿掉帧", ["卡顿", "显卡", "驱动", "温度", "内存"], "卡顿"),
    ("开机很慢怎么解决", ["开机", "启动项", "慢", "优化", "msconfig"], "卡顿"),

    # --- C盘/磁盘空间 ---
    ("C盘空间满了怎么清理", ["C盘", "清理", "磁盘", "空间", "删除"], "C盘"),
    ("C盘爆红了怎么办", ["C盘", "清理", "空间", "磁盘", "临时"], "C盘"),
    ("电脑磁盘空间不足", ["磁盘", "空间", "清理", "C盘", "删除"], "C盘"),
    ("如何释放C盘空间", ["C盘", "空间", "清理", "磁盘", "删除"], "C盘"),
    ("桌面文件太多C盘满了", ["C盘", "桌面", "空间", "清理", "迁移"], "C盘"),

    # --- 网络/上网 ---
    ("连不上WiFi怎么办", ["WiFi", "网络", "连接", "驱动", "路由"], "网络"),
    ("电脑无法上网", ["上网", "网络", "连接", "DNS", "IP", "网卡"], "网络"),
    ("WiFi连上但没网速", ["WiFi", "网络", "DNS", "IP", "路由"], "网络"),
    ("网络频繁掉线", ["网络", "掉线", "驱动", "路由", "信号"], "网络"),
    ("DNS解析失败怎么处理", ["DNS", "网络", "解析", "IP", "命令"], "网络"),
    ("宽带连接报错651", ["宽带", "651", "网络", "连接", "拨号"], "网络"),

    # --- 弹窗/广告/流氓软件 ---
    ("老是弹出广告怎么办", ["弹窗", "广告", "流氓", "软件", "卸载"], "弹窗"),
    ("电脑总是弹窗", ["弹窗", "广告", "软件", "卸载", "拦截"], "弹窗"),
    ("如何关闭垃圾弹窗", ["弹窗", "广告", "关闭", "软件", "拦截"], "弹窗"),
    ("被流氓软件绑架了", ["流氓", "软件", "卸载", "弹窗", "广告"], "弹窗"),

    # --- DLL/报错 ---
    ("提示缺少dll文件", ["dll", "文件", "缺失", "重新", "安装", "注册"], "DLL"),
    ("msvcr120.dll丢失", ["dll", "120", "运行库", "安装", "VC"], "DLL"),
    ("0xc000007b应用程序错误", ["0xc000007b", "7b", "运行库", "dll", "兼容"], "DLL"),
    ("电脑老是弹出错误代码", ["错误", "代码", "弹窗", "蓝屏", "报错"], "DLL"),

    # --- 驱动/硬件 ---
    ("显卡驱动怎么更新", ["显卡", "驱动", "更新", "官网", "设备管理器"], "驱动"),
    ("声卡没声音了", ["声音", "声卡", "驱动", "音量", "设备"], "驱动"),
    ("鼠标键盘没反应", ["鼠标", "键盘", "USB", "驱动", "接口"], "驱动"),
    ("笔记本风扇声音很大", ["风扇", "声音", "散热", "清灰", "温度"], "驱动"),

    # --- 系统/更新 ---
    ("Windows更新失败怎么办", ["更新", "Windows", "失败", "网络", "重试"], "系统"),
    ("怎么重装系统", ["重装", "系统", "U盘", "备份", "安装"], "系统"),
    ("系统激活失败", ["激活", "系统", "密钥", "正版", "错误"], "系统"),
    ("电脑开机自动修复", ["自动修复", "开机", "修复", "系统", "重启"], "系统"),

    # --- 软件/办公 ---
    ("软件安装失败", ["安装", "软件", "失败", "权限", "兼容"], "软件"),
    ("浏览器打不开网页", ["浏览器", "网页", "网络", "DNS", "缓存"], "软件"),
    ("Word文档打不开", ["Word", "文档", "打不开", "损坏", "修复"], "软件"),
    ("输入法不见了", ["输入法", "消失", "设置", "语言", "重启"], "软件"),

    # --- 边界 / 非电脑问题（应友好提示或低置信度）---
    ("今天天气怎么样", [], "边界"),
    ("推荐一部好看的电影", [], "边界"),
    ("帮我写一首诗", [], "边界"),
    ("怎么做红烧肉", [], "边界"),
    ("明天会下雨吗", [], "边界"),
]


def run_tests(verbose: bool = False):
    engine = get_engine()
    total = len(TEST_CASES)
    passed = 0
    details = []

    # 边界类问题不计入“命中率”统计（它们本身就是未命中预期）
    eval_cases = [c for c in TEST_CASES if c[2] != "边界"]
    eval_total = len(eval_cases)

    print(f"开始测试：共 {total} 个用例（其中 {eval_total} 个计入召回率）\n")

    for i, (question, keywords, category) in enumerate(TEST_CASES, 1):
        t0 = time.time()
        try:
            res = engine.ask(question)
            answer = (res.get("answer") or "").lower()
            is_boundary = (category == "边界")

            hit = False
            matched_kw = []
            if is_boundary:
                # 边界问题：期望 success=False 或 低置信度 + 无具体知识ID
                if (not res.get("success")) or res.get("confidence") == "low" or res.get("knowledge_id") is None:
                    hit = True
                    matched_kw = ["(友好提示/未命中)"]
            else:
                for kw in keywords:
                    if kw.lower() in answer:
                        hit = True
                        matched_kw.append(kw)
                        break

            cost = round((time.time() - t0) * 1000)
            status = "✓" if hit else "✗"
            if hit:
                passed += 1 if not is_boundary else 0
            if is_boundary and hit:
                # 边界命中不计入 passed 但有助展示
                pass

            details.append({
                "idx": i, "question": question, "category": category,
                "hit": hit, "matched": matched_kw, "layer": res.get("layer"),
                "confidence": res.get("confidence"), "cost_ms": cost,
            })

            if verbose:
                print(f"  [{i:2d}] {status} [{category}] \"{question}\"")
                print(f"       匹配层={res.get('layer')} 置信度={res.get('confidence')} "
                      f"耗时={cost}ms 命中词={matched_kw}")
        except Exception as e:
            details.append({"idx": i, "question": question, "category": category,
                            "hit": False, "error": str(e)})
            if verbose:
                print(f"  [{i:2d}] ✗ 异常: \"{question}\" -> {e}")

    # 召回率：仅统计非边界用例
    boundary_hits = sum(1 for d in details if d["category"] == "边界" and d["hit"])
    eval_hits = sum(1 for d in details if d["category"] != "边界" and d["hit"])
    recall = (eval_hits / eval_total * 100) if eval_total else 0.0

    # 按层级统计命中
    layer_stats = {}
    for d in details:
        if d["category"] != "边界" and d["hit"]:
            layer = d.get("layer", "unknown")
            layer_stats[layer] = layer_stats.get(layer, 0) + 1

    print("\n" + "=" * 50)
    print("测试结果汇总")
    print("=" * 50)
    print(f"总用例数      : {total}")
    print(f"计入召回率    : {eval_total}")
    print(f"召回命中      : {eval_hits}/{eval_total}")
    print(f"召回率        : {recall:.1f}%  (目标 >= 85%)")
    print(f"边界友好提示  : {boundary_hits} 个")
    print(f"各层命中分布  : {layer_stats}")
    print(f"结论          : {'✅ 达标' if recall >= 85 else '❌ 未达标'}")

    return {
        "total": total, "eval_total": eval_total, "eval_hits": eval_hits,
        "recall": round(recall, 1), "boundary_hits": boundary_hits,
        "layer_stats": layer_stats, "passed": recall >= 85, "details": details,
    }


def main():
    parser = argparse.ArgumentParser(description="电脑医生 AI 引擎自动化测试")
    parser.add_argument("--verbose", action="store_true", help="详细输出每条用例")
    parser.add_argument("--export", metavar="PATH", help="导出 JSON 报告到指定路径")
    parser.add_argument("--exit-on-fail", action="store_true", help="CI 模式：召回率不达标则非零退出")
    args = parser.parse_args()

    report = run_tests(verbose=args.verbose)

    if args.export:
        with open(args.export, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n报告已导出: {args.export}")

    if args.exit_on_fail and not report["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
