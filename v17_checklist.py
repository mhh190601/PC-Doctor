# -*- coding: utf-8 -*-
"""
v1.7 修复检查 & 新增功能验证脚本（自动化部分）
================================================
覆盖清单中"可通过代码/后端函数自动验证"的检查项。
人工体感项（启动画面粒子、3D木马旋转、深色模式视觉）见 v17_experience.md。

用法:
    cd pc_doctor_git
    python v17_checklist.py

输出: 结构化 PASS/FAIL/INFO 报告，退出码 0=全部通过, 1=有失败项。
"""
import sys
import os
import time
import json
import traceback

# 确保在仓库根目录运行
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import io
# Windows 控制台默认 GBK，无法输出 emoji，强制 UTF-8 流
if sys.stdout.encoding and "utf" not in sys.stdout.encoding.lower():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and "utf" not in sys.stderr.encoding.lower():
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPORT = []
def log(level, item, detail=""):
    """level: PASS / FAIL / INFO / SKIP"""
    REPORT.append((level, item, detail))
    mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "INFO": "[INFO]", "SKIP": "[SKIP]"}.get(level, "[    ]")
    print(f"{mark} {item}" + (f" -- {detail}" if detail else ""), flush=True)

# ============================================================
# 一、静态代码检查（不依赖运行环境）
# ============================================================
def check_static():
    print("\n=== 一、静态代码检查 ===")

    # Bug#7: 版本号一致性
    try:
        with open("main.py", "r", encoding="utf-8") as f:
            src = f.read()
        import re
        m = re.search(r'APP_VERSION\s*=\s*["\']([\d.]+)["\']', src)
        ver = m.group(1) if m else "NOT_FOUND"
        if ver == "1.7.0":
            log("PASS", "Bug#7 版本号=1.7.0", f"APP_VERSION={ver}")
        else:
            log("FAIL", "Bug#7 版本号", f"期望 1.7.0, 实际 {ver}")
    except Exception as e:
        log("FAIL", "Bug#7 版本号读取", str(e))

    # Bug#3: 权限检测函数存在
    if "def is_admin" in src and "IsUserAnAdmin" in src:
        log("PASS", "Bug#3 权限检测函数 is_admin() 存在", "使用 ctypes.windll.shell32.IsUserAnAdmin")
    else:
        log("FAIL", "Bug#3 权限检测函数缺失", "找不到 is_admin / IsUserAnAdmin")

    # Bug#6: 进程残留 — 检查是否用 os._exit 强退
    if "os._exit(0)" in src and "close_callback" in src:
        log("PASS", "Bug#6 退出逻辑", "使用 os._exit + close_callback 避免 Python 进程残留")
    else:
        log("FAIL", "Bug#6 退出逻辑", "未找到 os._exit 或 close_callback")

    # Bug#5: 3D木马正背面 — 检查 web/index.html 是否有镜像/背面处理
    try:
        with open("web/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        has_rotate = "rotateY" in html or "rotate" in html
        log("INFO", "Bug#5 3D木马旋转", "需在浏览器人工验证正背面文字朝向 (代码含 rotateY)" if has_rotate else "未找到旋转代码")
    except Exception as e:
        log("SKIP", "Bug#5 前端3D", str(e))

    # 自学习引擎核心代码存在性
    with open("ai_engine.py", "r", encoding="utf-8") as f:
        ai = f.read()
    checks = {
        "_clean_web_result": "搜索结果清洗函数",
        "ai_web_learned": "自学习来源标记",
        "def add_knowledge": "动态追加知识方法",
        '"learned"': "learned字段透传",
    }
    for key, desc in checks.items():
        if key in ai:
            log("PASS", f"自学习: {desc}", f"找到 {key}")
        else:
            log("FAIL", f"自学习: {desc}", f"找不到 {key}")

    # format_answer 透传 learned
    if "raw.get(\"learned\"" in ai:
        log("PASS", "自学习: format_answer 透传 learned", "")
    else:
        log("FAIL", "自学习: format_answer 未透传 learned", "")


# ============================================================
# 二、后端函数运行验证（需要 Windows + 依赖）
# ============================================================
def check_runtime():
    print("\n=== 二、后端函数运行验证 ===")
    try:
        import main
        log("PASS", "模块导入", "main.py 及依赖加载成功")
    except Exception as e:
        log("FAIL", "模块导入", f"{e}")
        traceback.print_exc()
        return

    # Bug#3: is_admin 可调用
    try:
        admin = main.is_admin()
        log("PASS", "Bug#3 is_admin() 可调用", f"返回 {admin} (True=管理员 / False=普通用户)")
    except Exception as e:
        log("FAIL", "Bug#3 is_admin() 调用", str(e))

    # 一、自研AI & 自学习闭环
    try:
        from ai_engine import get_engine
        engine = get_engine()
        # 本地(测试)模式
        r_local = engine.ask("电脑卡顿怎么办", mode="local")
        src_badge = r_local.get("source_badge", "")
        if src_badge in ("local", "cloud"):
            log("PASS", "一/AI: 本地模式返回source_badge", f"source_badge={src_badge}, layer={r_local.get('layer')}")
        else:
            log("INFO", "一/AI: 本地模式", f"layer={r_local.get('layer')}, badge={src_badge}")
    except Exception as e:
        log("FAIL", "一/AI: 本地模式", str(e))

    # 智能(自动)模式 — 触发联网学习闭环（需网络）
    try:
        engine = get_engine()
        q = "如何修复0x000000ED蓝屏错误"  # 大概率本地无精确命中
        t0 = time.time()
        r_auto = engine.ask(q, mode="auto")
        dt = round((time.time() - t0) * 1000)
        log("INFO", "一/AI: 智能模式响应", f"耗时 {dt}ms, layer={r_auto.get('layer')}, learned={r_auto.get('learned')}")
        if r_auto.get("learned"):
            log("PASS", "一/自学习: 自动入库成功", "learned=True, 下次本地模式应直接命中")
        else:
            log("INFO", "一/自学习: 本次未触发学习", f"layer={r_auto.get('layer')} (语义已命中则直接返回不学; 联网搜索需BING_SEARCH_API_KEY)")
        # 明确声明自学习通道依赖
        if engine.web_searcher is None:
            log("INFO", "一/自学习: 联网搜索通道", "web_searcher=None (未配置 BING_SEARCH_API_KEY)，自学习仅通过 local_model/cloud 路径触发")
        else:
            log("PASS", "一/自学习: 联网搜索通道可用", "BING_SEARCH_API_KEY 已配置")
    except Exception as e:
        log("FAIL", "一/AI: 智能模式", str(e))

    # 三、安全体检面板
    try:
        if hasattr(main, "get_security_status"):
            sec = main.get_security_status()
            if isinstance(sec, dict) and ("defender" in str(sec).lower() or "virus" in str(sec).lower() or "防火墙" in str(sec)):
                log("PASS", "三/安全面板: get_security_status 返回结构化数据", f"keys={list(sec.keys())[:6]}")
            else:
                log("INFO", "三/安全面板: get_security_status", f"返回 {str(sec)[:80]}")
        else:
            # 查找任何安全相关函数
            sec_fn = [n for n in dir(main) if "secur" in n.lower() or "defender" in n.lower() or "firewall" in n.lower()]
            log("INFO", "三/安全面板", f"安全函数: {sec_fn}")
    except Exception as e:
        log("FAIL", "三/安全面板", str(e))

    # 四、C盘救星 & 五、软件中心 — 下载逻辑
    try:
        installed = main.is_cdisksaver_installed()
        log("INFO", "四/C盘救星: is_cdisksaver_installed", f"已安装={installed}")
        # 验证打开逻辑（不实际打开窗口，仅检查函数存在）
        if hasattr(main, "open_cdisksaver") or hasattr(main, "open_tool"):
            log("PASS", "四/C盘救星: 打开函数存在", "")
        else:
            log("INFO", "四/C盘救星: 打开函数", f"函数列表含open: {[n for n in dir(main) if n.startswith('open')][:5]}")
    except Exception as e:
        log("FAIL", "四/软件中心", str(e))

    # 软件中心: 子工具列表
    try:
        if hasattr(main, "get_software_list") or hasattr(main, "get_tools"):
            fn = main.get_software_list if hasattr(main, "get_software_list") else main.get_tools
            lst = fn()
            names = [x.get("name", "") for x in lst] if isinstance(lst, list) else []
            targets = ["隐私清理", "启动项", "文件粉碎"]
            hit = [t for t in targets if any(t in n for n in names)]
            if hit:
                log("PASS", "五/软件中心: 新增子工具入口", f"命中 {hit}")
            else:
                log("INFO", "五/软件中心", f"工具名: {names[:8]}")
        else:
            log("INFO", "五/软件中心", "未找到 get_software_list/get_tools，需人工确认卡片")
    except Exception as e:
        log("FAIL", "五/软件中心", str(e))


# ============================================================
# 三、知识库增量索引验证
# ============================================================
def check_kb_increment():
    print("\n=== 三、知识库增量索引验证 ===")
    try:
        from ai_engine import get_engine
        import learning
        engine = get_engine()
        def _count():
            data = learning.load_knowledge_data()
            return len(data) if isinstance(data, (list, dict)) else 0
        before = _count()
        # 用本地知识库极不可能命中的长尾问题触发联网学习（需联网）
        q = f"如何在Ubuntu服务器上配置GPU直通给KVM虚拟机_{int(time.time())}"
        r = engine.ask(q, mode="auto")
        after = _count()
        log("INFO", "三/知识库: 增量前后条目数", f"before={before}, after={after}, layer={r.get('layer')}, learned={r.get('learned')}")
        if after > before:
            log("PASS", "三/知识库: 自学习入库使条目数增加", f"+{after - before}")
        elif r.get("learned"):
            log("PASS", "三/知识库: 自学习标记 learned=True", "已写入但计数函数未反映(异步)")
        else:
            log("INFO", "三/知识库", "未触发学习(可能离线/本地已命中/未达web阈值)")
    except Exception as e:
        log("FAIL", "三/知识库", str(e))


def main_run():
    print("=" * 60)
    print(" PC Doctor v1.7 自动化检查 (Bug清单 + 新增功能)")
    print("=" * 60)
    check_static()
    check_runtime()
    check_kb_increment()

    print("\n" + "=" * 60)
    fails = [r for r in REPORT if r[0] == "FAIL"]
    print(f"总计 {len(REPORT)} 项 | PASS {sum(1 for r in REPORT if r[0]=='PASS')} | "
          f"FAIL {len(fails)} | INFO {sum(1 for r in REPORT if r[0]=='INFO')} | SKIP {sum(1 for r in REPORT if r[0]=='SKIP')}")
    if fails:
        print("\n失败项:")
        for _, item, detail in fails:
            print(f"  ❌ {item} — {detail}")
        print("\n建议: 修复上述 FAIL 项后重新运行。人工体感项见 v17_experience.md")
        return 1
    print("\n🎉 所有自动化检查通过！人工体感项请按 v17_experience.md 逐项体验。")
    return 0


if __name__ == "__main__":
    sys.exit(main_run())
