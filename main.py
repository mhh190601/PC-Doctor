"""
电脑医生桌面版 - 带自学习AI的本地优化工具
"""

import sys
import os
import warnings
# 抑制第三方库(eel/pyparsing) API 废弃警告 — VS2026 输出窗口会捕获 stderr
warnings.filterwarnings('ignore', module='pyparsing')
import json
import eel
import subprocess
import shutil

# Windows: 禁止子进程弹出CMD窗口（0x08000000 = CREATE_NO_WINDOW）
CREATE_NO_WINDOW = 0x08000000
import tempfile
import socket
import threading
import psutil
import hashlib
import re
import winreg
import requests
import webbrowser
import time
import random
import string
import ssl
import urllib.request
import urllib.error
import logging
import ctypes
from ctypes import windll, wintypes
import learning  # 导入自学习模块

# 任务7：知识库加载就绪事件，用于启动加速（延迟加载，主窗口提前出现）
kb_ready = threading.Event()

# 确保模块所在目录在 sys.path，便于 Pylance 静态解析同目录模块（如 offtopic）
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

logger = logging.getLogger('pc_doctor')
import stat

# PyInstaller 打包兼容：确保临时解压目录在路径中
if getattr(sys, 'frozen', False):
    application_path = sys._MEIPASS
    os.chdir(application_path)
    sys.path.insert(0, application_path)

# 初始化知识库（启动时自动建表并加载）
learning.init_db()
learning.refresh_cache()

# 使用绝对路径，避免工作目录问题
eel.init(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web'))

def _get_data_dir():
    """获取数据文件持久化目录（exe 取可执行文件所在目录，源码取脚本目录）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

KB_PATH = os.path.join(_get_data_dir(), 'knowledge_base.json')
KB_V2_PATH = os.path.join(_get_data_dir(), 'knowledge_base_v2.json')

# ================== 原有优化功能 (保持不变) ==================
THEME_FILE = os.path.join(os.getenv('APPDATA', os.path.expanduser('~')), 'PC-Doctor', 'theme_pref.txt')

@eel.expose
def get_theme():
    """获取用户保存的主题 'light' 或 'dark'"""
    if os.path.exists(THEME_FILE):
        with open(THEME_FILE, 'r') as f:
            return f.read().strip()
    return 'light'

@eel.expose
def set_theme(theme):
    """保存主题偏好"""
    os.makedirs(os.path.dirname(THEME_FILE), exist_ok=True)
    with open(THEME_FILE, 'w') as f:
        f.write(theme)

@eel.expose
def clean_temp_files():
    results = []
    temp_locations = [
        tempfile.gettempdir(),
        os.path.expandvars(r'%SystemRoot%\Temp'),
        os.path.expandvars(r'%SystemRoot%\Prefetch'),
    ]
    total_deleted = 0
    for temp_dir in temp_locations:
        if not os.path.exists(temp_dir):
            continue
        deleted = 0
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    os.remove(file_path)
                    deleted += 1
                except (PermissionError, OSError):
                    pass
        try:
            for root, dirs, files in os.walk(temp_dir, topdown=False):
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    try:
                        os.rmdir(dir_path)
                        deleted += 1
                    except OSError:
                        pass
        except Exception:
            pass
        results.append(f"清理 {temp_dir}: 删除了 {deleted} 个文件/文件夹")
        total_deleted += deleted
    results.append(f"\n总计清理 {total_deleted} 项临时文件。")
    return "\n".join(results)

@eel.expose
def run_disk_cleanup():
    try:
        subprocess.run(['cleanmgr', '/sagerun:1'], capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
        return "Windows 磁盘清理已完成。"
    except FileNotFoundError:
        return "错误：找不到磁盘清理程序，仅支持 Windows。"
    except subprocess.TimeoutExpired:
        return "磁盘清理超时，请手动运行 cleanmgr。"
    except Exception as e:
        return f"磁盘清理失败: {str(e)}"

@eel.expose
def check_startup_items():
    import winreg
    startup_list = []
    keys = [
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
    ]
    for key_path in keys:
        for hive, label in [(winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")]:
            key = None
            try:
                key = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ)
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                        startup_list.append(f"[{label}] {name}: {value}")
                        i += 1
                    except OSError:
                        break
            except Exception:
                pass
            finally:
                if key is not None:
                    winreg.CloseKey(key)
    if not startup_list:
        return "没有发现额外的开机启动项。"
    else:
        return "发现以下开机启动项：\n" + "\n".join(startup_list) + "\n\n如需禁用，请手动打开任务管理器 → 启动标签，右键禁用。"

@eel.expose
def check_disk_space():
    import string
    from ctypes import windll
    drives = []
    bitmask = windll.kernel32.GetLogicalDrives()
    for letter in string.ascii_uppercase:
        if bitmask & 1:
            drives.append(letter + ':\\')
        bitmask >>= 1
    report_lines = []
    for drive in drives:
        try:
            total, used, free = shutil.disk_usage(drive)
            total_gb = total / (1024 ** 3)
            free_gb = free / (1024 ** 3)
            percent_free = (free / total) * 100
            if percent_free < 10:
                report_lines.append(f"⚠️ {drive} 剩余 {free_gb:.1f}GB / {total_gb:.1f}GB ({percent_free:.1f}%) - 空间紧张！")
            else:
                report_lines.append(f"✅ {drive} 剩余 {free_gb:.1f}GB / {total_gb:.1f}GB ({percent_free:.1f}%)")
        except Exception:
            pass
    return "\n".join(report_lines)

@eel.expose
def check_rogue_software():
    bad_paths = [
        r'C:\Program Files (x86)\360',
        r'C:\Program Files\360',
        os.path.expandvars(r'%APPDATA%\360'),
        os.path.expandvars(r'%LOCALAPPDATA%\2345'),
        os.path.expandvars(r'%ProgramFiles%\2345'),
        os.path.expandvars(r'%ProgramFiles(x86)%\2345'),
    ]
    found = []
    for path in bad_paths:
        if os.path.exists(path):
            found.append(path)
    if found:
        return "发现以下可疑软件目录：\n" + "\n".join(found) + "\n建议在'应用和功能'中卸载它们。"
    else:
        return "未发现常见流氓软件痕迹，你的电脑很干净。"

@eel.expose
def check_dns():
    try:
        socket.gethostbyname('www.baidu.com')
        return "DNS 解析正常，网络连接良好。"
    except socket.gaierror:
        return "DNS 解析异常！可尝试将 DNS 修改为 114.114.114.114 或 223.5.5.5。"

@eel.expose
def get_drives():
    """返回系统所有可用盘符列表，如 ['C:', 'D:', 'E:']"""
    drives = []
    bitmask = windll.kernel32.GetLogicalDrives()
    for letter in string.ascii_uppercase:
        if bitmask & 1:
            drives.append(f"{letter}:\\")
        bitmask >>= 1
    return drives

@eel.expose
def get_system_status():
    """获取系统状态信息（CPU、内存）"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        return {
            'cpu_percent': cpu_percent,
            'memory_percent': memory.percent,
            'memory_used_gb': round(memory.used / (1024**3), 1),
            'memory_total_gb': round(memory.total / (1024**3), 1)
        }
    except Exception as e:
        return {'error': str(e)}


@eel.expose
def is_admin():
    """检测当前是否以管理员权限运行"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def _find_nvidia_smi():
    """查找 nvidia-smi.exe 的完整路径"""
    paths = [
        os.path.join(os.environ.get('ProgramFiles', 'C:\\Program Files'), 'NVIDIA Corporation', 'NVSMI', 'nvidia-smi.exe'),
        os.path.join(os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)'), 'NVIDIA Corporation', 'NVSMI', 'nvidia-smi.exe'),
        os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'System32', 'nvidia-smi.exe'),
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    # 搜索 DriverStore
    try:
        driver_store = os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'System32', 'DriverStore', 'FileRepository')
        if os.path.exists(driver_store):
            for root, dirs, files in os.walk(driver_store):
                if 'nvidia-smi.exe' in files:
                    return os.path.join(root, 'nvidia-smi.exe')
    except Exception:
        pass
    return None

@eel.expose
def get_cpu_temperature():
    """获取CPU温度（摄氏度），无需管理员权限。返回浮点数或 None"""
    try:
        # 方法1: Win32_PerfFormattedData_Counters_ThermalZoneInformation (无需管理员)
        # Temperature 字段是开尔文温度 (不是十分之一开尔文)
        result = subprocess.run(
            ['wmic', 'path', 'Win32_PerfFormattedData_Counters_ThermalZoneInformation',
             'get', 'Temperature'],
            capture_output=True, text=True, timeout=5, creationflags=CREATE_NO_WINDOW
        )
        lines = result.stdout.strip().split('\n')
        for line in lines[1:]:  # 跳过表头
            line = line.strip()
            if line.isdigit():
                kelvin = int(line)
                if 200 < kelvin < 400:  # 合理范围: -73°C ~ 127°C
                    temp_celsius = kelvin - 273.15
                    return round(temp_celsius, 1)
    except Exception:
        pass
    
    try:
        # 方法2: MSAcpi_ThermalZoneTemperature (需要管理员权限)
        result = subprocess.run(
            ['wmic', '/namespace:\\\\root\\wmi', 'PATH', 'MSAcpi_ThermalZoneTemperature', 'get', 'CurrentTemperature'],
            capture_output=True, text=True, timeout=5, creationflags=CREATE_NO_WINDOW
        )
        lines = result.stdout.strip().split('\n')
        if len(lines) >= 2:
            kelvin_tenths = lines[1].strip()
            if kelvin_tenths.isdigit():
                temp_celsius = (int(kelvin_tenths) / 10.0) - 273.15
                if -20 < temp_celsius < 150:
                    return round(temp_celsius, 1)
    except Exception:
        pass
    return None

_nvidia_smi_path = None

@eel.expose
def get_gpu_temperature():
    """获取NVIDIA GPU温度（摄氏度），如果失败返回 None"""
    global _nvidia_smi_path
    try:
        if _nvidia_smi_path is None:
            _nvidia_smi_path = _find_nvidia_smi()
        if _nvidia_smi_path is None:
            return None
        result = subprocess.run(
            [_nvidia_smi_path, '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5, creationflags=CREATE_NO_WINDOW
        )
        temp_str = result.stdout.strip()
        if temp_str:
            return round(float(temp_str), 1)
    except Exception:
        pass
    return None

@eel.expose
def get_temperatures():
    """获取CPU和GPU温度，返回字典"""
    cpu_temp = get_cpu_temperature()
    gpu_temp = get_gpu_temperature()
    return {
        'cpu': cpu_temp,
        'gpu': gpu_temp
    }

@eel.expose
def optimize_memory():
    """
    内存优化：清理进程工作集，释放可交换的物理内存。
    需要管理员权限才能对所有进程生效，普通用户权限也能清理部分进程。
    """
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        
        OpenProcess = kernel32.OpenProcess
        OpenProcess.restype = wintypes.HANDLE
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        
        CloseHandle = kernel32.CloseHandle
        
        EmptyWorkingSet = psapi.EmptyWorkingSet
        EmptyWorkingSet.restype = wintypes.BOOL
        EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        
        PROCESS_ALL_ACCESS = 0x1F0FFF
        released_count = 0
        
        for proc in psutil.process_iter(['pid']):
            try:
                pid = proc.info['pid']
                handle = OpenProcess(PROCESS_ALL_ACCESS, False, pid)
                if handle:
                    try:
                        if EmptyWorkingSet(handle):
                            released_count += 1
                    except Exception:
                        pass
                    finally:
                        CloseHandle(handle)
            except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                continue
                
        return f"内存优化完成，已清理 {released_count} 个进程的工作内存。\n\n💡 提示：\n• 释放的内存是暂时不用的物理内存，不会影响正在运行的程序。\n• 如果以管理员身份运行本软件，清理效果会更好。"
    except Exception as e:
        return f"内存优化失败：{str(e)}\n请尝试以管理员身份运行本软件。"

@eel.expose
def create_system_restore_point(description="电脑医生自动还原点"):
    """
    创建系统还原点，需要管理员权限。
    返回成功或失败信息。
    """
    try:
        # 在创建还原点之前，先启用系统保护（如果未启用）
        subprocess.run(
            ['powershell', '-Command', 'Enable-ComputerRestore -Drive "C:\\"'],
            capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW
        )
        # 创建还原点（对 description 做安全转义，防止命令注入）
        safe_desc = description.replace('"', '""')
        result = subprocess.run(
            ['powershell', '-Command', f'Checkpoint-Computer -Description "{safe_desc}" -RestorePointType "MODIFY_SETTINGS"'],
            capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW
        )
        if result.returncode == 0:
            return {"success": True, "message": f"系统还原点创建成功！\n描述：{description}"}
        else:
            return {"success": False, "message": f"创建失败：{result.stderr}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "创建还原点超时，请重试。"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@eel.expose
def run_all_optimizations():
    report_parts = []
    report_parts.append("=== 临时文件清理 ===")
    report_parts.append(clean_temp_files())
    report_parts.append("\n=== 磁盘清理 ===")
    report_parts.append(run_disk_cleanup())
    report_parts.append("\n=== 开机启动项检查 ===")
    report_parts.append(check_startup_items())
    report_parts.append("\n=== 磁盘空间检查 ===")
    report_parts.append(check_disk_space())
    report_parts.append("\n=== 流氓软件检查 ===")
    report_parts.append(check_rogue_software())
    report_parts.append("\n=== DNS 检查 ===")
    report_parts.append(check_dns())
    report_parts.append("\n=== 内存优化 ===")
    try:
        report_parts.append(optimize_memory())
    except Exception as e:
        report_parts.append(f"内存优化失败：{e}")
    report_parts.append("\n🎉 所有优化项目已完成！")
    return "\n".join(report_parts)

# ================== 自学习AI诊断接口 ==================
@eel.expose
def ai_diagnose(problem_description, mode="auto"):
    """智能诊断：多模式切换（auto/local/cloud/search），纯本地优先、离线自动回退。

    模式说明：
      local  —— 仅本地检索（语义/标签/自学习+本地模型），完全离线
      cloud  —— 仅云端 API（断网/禁用时自动回退本地并友好提示）
      search —— 仅联网搜索（断网/禁用时自动回退本地）
      auto   —— 本地优先，本地相似度低于 config.local_threshold 时转云端联网
    """
    from ai_engine import get_engine, format_answer, is_online
    from offtopic import is_pc_related  # pyright: ignore[reportMissingImports]
    from empathy_engine import compose_reply, empathy_intro  # pyright: ignore[reportMissingImports]

    # 任务7：确保后台知识库加载完成后再进行 AI 诊断（避免空库诊断）
    if not kb_ready.is_set():
        kb_ready.wait(timeout=30)

    # 电脑问题 + 情绪同时出现 → 先共情再走正常诊断（不干扰诊断结果）
    empathy_prefix = empathy_intro(problem_description)

    def _pack(diagnostic_result: dict) -> dict:
        """打包诊断结果，若含共情前缀则拼到答案最前（先共情后解决）。"""
        if empathy_prefix:
            diagnostic_result = dict(diagnostic_result)
            diagnostic_result["answer"] = empathy_prefix + "\n\n" + diagnostic_result.get("answer", "")
        return _pack_ai_result(diagnostic_result)

    def _append_note(res: dict, note: str) -> dict:
        """在答案末尾追加说明（不破坏原有字段）。"""
        if not res:
            return res
        res = dict(res)
        res["answer"] = (res.get("answer") or "") + "\n\n" + note
        return res

    def _friendly(reason: str) -> dict:
        """统一友好提示封装（异常/禁用/失败均走这里，不向前端抛错误对象）。"""
        return _pack_ai_result({
            "success": False,
            "answer": f"抱歉，暂时没法处理你的问题（{reason}）。你可以换种说法，或稍后再试。",
            "layer": "error", "type": "error", "score": 0.0,
            "confidence": "low", "source": "", "tags": "", "severity": "低",
            "layer_label": "提示",
        })

    # 非电脑相关问题 → 走高情商本地陪聊模块（方案A+，纯本地、零依赖、毫秒级）
    try:
        if not is_pc_related(problem_description):
            answer = compose_reply(problem_description)
            result = format_answer({
                "success": True,
                "answer": answer,
                "layer": "empathy",
                "type": "empathy",
                "score": 1.0,
                "confidence": "high",
                "source": "empathy",
                "tags": "",
                "severity": "低",
                "layer_label": "轻松陪聊",
            })
            return _pack(result)
    except Exception as e:
        logger.error(f"陪聊分支异常: {e}")

    # 电脑问题：按模式分流
    try:
        engine = get_engine()
        cfg = engine.config
        enable_online = bool(cfg.get("enable_online", True))
        local_threshold = float(cfg.get("local_threshold", 0.5))

        mode = (mode or "auto").lower()

        # cloud：仅云端 API
        if mode == "cloud":
            if not enable_online:
                return _friendly("云端模式已在 config.json 中禁用（enable_online=false）")
            if not is_online():
                # 断网 → 自动回退本地，不报联网错误
                local_res = engine.ask(problem_description, mode="local")
                return _pack(_append_note(local_res, "（检测到离线，已自动回退本地模式）"))
            return _pack(engine.ask(problem_description, mode="cloud"))

        # search：仅联网搜索
        if mode == "search":
            if not enable_online:
                return _friendly("搜索模式已在 config.json 中禁用（enable_online=false）")
            if not is_online():
                local_res = engine.ask(problem_description, mode="local")
                return _pack(_append_note(local_res, "（检测到离线，已自动回退本地模式）"))
            return _pack(engine.ask(problem_description, mode="search"))

        # local：仅本地检索
        if mode == "local":
            return _pack(engine.ask(problem_description, mode="local"))

        # auto：本地优先，低于阈值转云端联网
        local_res = engine.ask(problem_description, mode="local")
        local_score = float(local_res.get("score", 0) or 0)
        if local_res.get("success") and local_score >= local_threshold:
            return _pack(local_res)
        # 本地不足 → 尝试云端联网补充
        if enable_online and is_online():
            try:
                cloud_res = engine.ask(problem_description, mode="cloud")
                if cloud_res.get("success"):
                    return _pack(_append_note(
                        cloud_res,
                        f"（本地匹配置信度 {local_score:.0%} 偏低，已自动转云端联网补充）",
                    ))
            except Exception as e:
                logger.error(f"auto 转云端失败: {e}")
        # 云端不可用 → 返回本地结果并提示
        return _pack(_append_note(
            local_res,
            "（本地未能高置信匹配，且当前无法联网补充，建议换种说法或检查网络）",
        ))
    except Exception as e:
        logger.error(f"ai_diagnose 异常: {e}")
        return _friendly("系统处理出错，请稍后重试")


def _pack_ai_result(result: dict) -> dict:
    """将 ai_engine 的结构化结果打包成前端需要的字段"""
    return {
        "success": result.get("success", False),
        "answer": result.get("answer", "") if result.get("success") else "",
        "message": "" if result.get("success") else result.get("answer", ""),
        "knowledge_id": result.get("knowledge_id"),
        "score": result.get("score", 0),
        "source": result.get("source", ""),
        "confidence": result.get("confidence", "low"),
        "tags": result.get("tags", ""),
        "severity": result.get("severity", ""),
        # 新增：前端增强展示字段
        "score_percent": result.get("score_percent", int(round(float(result.get("score", 0)) * 100))),
        "confidence_color": result.get("confidence_color", "red"),
        "risk_label": result.get("risk_label", ""),
        "severity_color": result.get("severity_color", ""),
        "has_source_url": result.get("has_source_url", False),
        "source_url": result.get("source_url", ""),
        "layer_label": result.get("layer_label", ""),
        "matched_tags": result.get("matched_tags", ""),
        "model_note": result.get("model_note", ""),
        "candidates": result.get("candidates", []),
    }

@eel.expose
def submit_feedback(knowledge_id, is_helpful, user_question):
    """提交用户反馈（由 AIEngine 统一处理权重/日志/统计，避免与 learning 双写冲突，bug #2）"""
    try:
        from ai_engine import get_engine
        engine = get_engine()
        engine.feedback(knowledge_id, is_helpful, user_question)
    except Exception as e:
        logger.error(f"提交反馈失败: {e}")
        return "反馈记录失败，请稍后再试"
    return "反馈已记录，感谢你的帮助！"

@eel.expose
def add_user_knowledge(question, answer):
    """用户补充新知识（由 AIEngine 统一写入，避免与 learning 双写）"""
    try:
        from ai_engine import get_engine
        engine = get_engine()
        engine.add_knowledge(question, answer, source='用户贡献', tags='用户贡献', severity='中')
    except Exception as e:
        logger.error(f"补充知识失败: {e}")
        return "新知识录入失败，请稍后再试"
    return "新知识已录入，谢谢你的贡献！"

# ================== 综合体检功能 ==================

def get_folder_size(folder_path):
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(folder_path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except (OSError, PermissionError):
                    pass
    except Exception:
        pass
    return total / (1024 * 1024)  # 返回 MB

@eel.expose
def full_system_scan():
    score = 100
    checks = []  # 详细检查项列表
    issues = []  # 仅包含有问题的项
    
    # ---------- 1. 磁盘空间 ----------
    try:
        disk_report = check_disk_space()
        # 分析 disk_report 字符串，提取空间紧张的分区
        has_low = '空间紧张' in disk_report or '不足' in disk_report
        if has_low:
            score -= 15
            status = 'error'
            desc = '部分磁盘可用空间低于10%，建议清理。'
            issues.append({"name": "磁盘空间不足", "level": "error", "desc": desc})
        else:
            status = 'ok'
            desc = '所有磁盘空间充足。'
        checks.append({
            "name": "磁盘空间检查",
            "status": status,
            "desc": desc
        })
    except Exception:
        checks.append({"name": "磁盘空间检查", "status": "warning", "desc": "检测失败"})
    
    # ---------- 2. 临时文件 ----------
    try:
        temp_size = 0
        import tempfile
        temp_locations = [tempfile.gettempdir(), os.path.expandvars(r'%SystemRoot%\Temp'), os.path.expandvars(r'%SystemRoot%\Prefetch')]
        for loc in temp_locations:
            if os.path.exists(loc):
                temp_size += get_folder_size(loc)
        if temp_size > 500:
            score -= 10
            status = 'warning'
            desc = f'临时文件约 {temp_size:.0f} MB，建议清理。'
            issues.append({"name": "垃圾文件过多", "level": "warning", "desc": desc})
        else:
            status = 'ok'
            desc = f'临时文件约 {temp_size:.0f} MB，状态良好。'
        checks.append({
            "name": "垃圾文件检查",
            "status": status,
            "desc": desc
        })
    except Exception:
        checks.append({"name": "垃圾文件检查", "status": "warning", "desc": "检测失败"})
    
    # ---------- 3. 启动项 ----------
    try:
        startup_info = check_startup_items()
        startup_count = startup_info.count('HKCU') + startup_info.count('HKLM')
        if startup_count > 15:
            score -= 15
            status = 'warning'
            desc = f'检测到 {startup_count} 个启动项，可能拖慢开机。'
            issues.append({"name": "开机启动项过多", "level": "warning", "desc": desc})
        else:
            status = 'ok'
            desc = f'启动项数量正常（{startup_count} 个）。'
        checks.append({
            "name": "开机启动项检查",
            "status": status,
            "desc": desc
        })
    except Exception:
        checks.append({"name": "开机启动项检查", "status": "warning", "desc": "检测失败"})
    
    # ---------- 4. 流氓软件 ----------
    try:
        rogue_result = check_rogue_software()
        if '发现以下' in rogue_result:
            score -= 20
            status = 'error'
            desc = '发现可疑软件残留，建议处理。'
            issues.append({"name": "发现流氓软件", "level": "error", "desc": desc})
        else:
            status = 'ok'
            desc = '未发现流氓软件痕迹。'
        checks.append({
            "name": "流氓软件检查",
            "status": status,
            "desc": desc
        })
    except Exception:
        checks.append({"name": "流氓软件检查", "status": "warning", "desc": "检测失败"})
    
    # ---------- 5. DNS ----------
    try:
        dns_result = check_dns()
        if '异常' in dns_result:
            score -= 10
            status = 'warning'
            desc = 'DNS 解析异常，可能影响上网。'
            issues.append({"name": "DNS异常", "level": "warning", "desc": desc})
        else:
            status = 'ok'
            desc = 'DNS 解析正常。'
        checks.append({
            "name": "DNS 检查",
            "status": status,
            "desc": desc
        })
    except Exception:
        checks.append({"name": "DNS 检查", "status": "warning", "desc": "检测失败"})
    
    # ---------- 6. 内存使用率 ----------
    try:
        mem = psutil.virtual_memory()
        mem_percent = mem.percent
        if mem_percent > 85:
            score -= 10
            status = 'warning'
            desc = f'当前内存使用率 {mem_percent}%，建议关闭不必要程序。'
            issues.append({"name": "内存占用过高", "level": "warning", "desc": desc})
        else:
            status = 'ok'
            desc = f'内存使用率 {mem_percent}%，正常。'
        checks.append({
            "name": "内存使用率检查",
            "status": status,
            "desc": desc
        })
    except Exception:
        checks.append({"name": "内存使用率检查", "status": "warning", "desc": "检测失败"})
    
    # ---------- 7. 开机耗时 ----------
    try:
        boot_info = get_boot_info()
        last_boot_time = boot_info.get('last_boot_time', '')
        if '分钟' in last_boot_time:
            try:
                boot_mins = int(last_boot_time.split('分钟')[0])
                if boot_mins > 60:
                    score -= 10
                    status = 'warning'
                    desc = f'上次开机耗时 {last_boot_time}，较慢。'
                    issues.append({"name": "开机速度较慢", "level": "warning", "desc": desc})
                else:
                    status = 'ok'
                    desc = f'上次开机耗时 {last_boot_time}。'
            except (ValueError, IndexError):
                status = 'ok'
                desc = f'上次开机耗时 {last_boot_time}。'
        else:
            status = 'ok'
            desc = f'上次开机耗时 {last_boot_time}。'
        checks.append({
            "name": "开机耗时检查",
            "status": status,
            "desc": desc
        })
    except Exception:
        checks.append({"name": "开机耗时检查", "status": "warning", "desc": "检测失败"})

    # ---------- 8. 安全体检（Windows 安全中心） ----------
    try:
        sec = get_security_status()
        sec_score = sec.get("score", 100)
        # 安全评分折算进综合体检评分（最高扣 20 分）
        if sec_score < 100:
            deduct = max(5, int(round((100 - sec_score) / 100.0 * 20)))
            score -= deduct
        for it in sec.get("items", []):
            name = it.get("name", "安全检查")
            ok = it.get("status") in ("ok", "enabled", True)
            status = "ok" if ok else ("error" if it.get("status") in ("error", "disabled", False) else "warning")
            desc = it.get("desc", "")
            checks.append({"name": f"安全 · {name}", "status": status, "desc": desc})
    except Exception:
        checks.append({"name": "安全体检", "status": "warning", "desc": "检测失败"})

    score = max(0, min(100, score))

    return {
        "score": score,
        "issues": issues,   # 仅问题列表
        "checks": checks    # 所有检查项详情
    }

# ================== 隐私清理 ==================
@eel.expose
def get_privacy_options():
    """
    返回可清理的隐私项列表，供前端展示
    """
    options = [
        {
            "id": "browser_cache",
            "name": "浏览器缓存 (Chrome/Edge)",
            "desc": "清理 Chrome 和 Edge 的缓存文件、Cookie、历史记录",
            "default": True
        },
        {
            "id": "recent_docs",
            "name": "最近使用的文档记录",
            "desc": "清除开始菜单和资源管理器中的最近文件记录",
            "default": True
        },
        {
            "id": "run_history",
            "name": "运行历史记录",
            "desc": "清除 Win+R 运行框中输入过的命令历史",
            "default": True
        },
        {
            "id": "recycle_bin",
            "name": "清空回收站",
            "desc": "彻底清空回收站中的所有文件",
            "default": True
        },
        {
            "id": "wechat_cache",
            "name": "微信/QQ 缓存",
            "desc": "清理微信和QQ的图片、视频缓存以及接收的文件",
            "default": False
        },
        {
            "id": "temp_files",
            "name": "系统临时文件",
            "desc": "清理 Windows 临时文件夹（等同于系统优化中的临时文件清理）",
            "default": True
        }
    ]
    return options

@eel.expose
def clean_privacy_items(selected_ids):
    """
    根据用户勾选的清理项ID，执行相应的清理操作
    返回清理结果信息
    """
    total_files = 0
    total_size = 0
    details = []

    def get_dir_size(path):
        size = 0
        if os.path.exists(path):
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        size += os.path.getsize(fp)
                    except Exception:
                        pass
        return size

    # 浏览器缓存清理 (Chrome 和 Edge 的常见缓存目录)
    if 'browser_cache' in selected_ids:
        browsers = {
            'Chrome': os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache'),
            'Edge': os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache'),
        }
        for name, cache_path in browsers.items():
            if os.path.exists(cache_path):
                size_before = get_dir_size(cache_path)
                try:
                    for f in os.listdir(cache_path):
                        fp = os.path.join(cache_path, f)
                        try:
                            if os.path.isfile(fp):
                                os.remove(fp)
                            else:
                                shutil.rmtree(fp, ignore_errors=True)
                        except Exception:
                            pass
                    size_after = get_dir_size(cache_path)
                    freed = size_before - size_after
                    total_files += 1
                    total_size += freed
                    details.append(f"清理 {name} 缓存，释放约 {freed / (1024*1024):.1f} MB")
                except Exception:
                    details.append(f"清理 {name} 缓存时失败")

    # 最近文档记录
    if 'recent_docs' in selected_ids:
        recent = os.path.expandvars(r'%APPDATA%\Microsoft\Windows\Recent')
        if os.path.exists(recent):
            count = 0
            for f in os.listdir(recent):
                try:
                    os.remove(os.path.join(recent, f))
                    count += 1
                except Exception:
                    pass
            total_files += count
            details.append(f"清理最近文档记录 {count} 条")

    # 运行历史
    if 'run_history' in selected_ids:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\RunMRU', 0, winreg.KEY_ALL_ACCESS)
            count = winreg.QueryInfoKey(key)[1]
            for _ in range(count):
                name = winreg.EnumValue(key, 0)[0]
                if name != 'MRUList':
                    winreg.DeleteValue(key, name)
            winreg.DeleteValue(key, 'MRUList')
            winreg.CloseKey(key)
            details.append("清理运行历史记录完成")
            total_files += 1
        except Exception:
            details.append("清理运行历史记录失败")

    # 清空回收站
    if 'recycle_bin' in selected_ids:
        try:
            subprocess.run(
                ['cmd', '/c', 'rd', '/s', '/q', os.path.expandvars(r'%systemdrive%\$Recycle.Bin')],
                capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW
            )
            details.append("回收站已清空")
        except Exception:
            details.append("清空回收站失败")

    # 微信/QQ 缓存
    if 'wechat_cache' in selected_ids:
        # 微信
        wechat_files = os.path.expandvars(r'%USERPROFILE%\Documents\WeChat Files')
        if os.path.exists(wechat_files):
            for user in os.listdir(wechat_files):
                cache_dirs = ['FileStorage/Image', 'FileStorage/Video', 'FileStorage/File']
                for cd in cache_dirs:
                    path = os.path.join(wechat_files, user, cd)
                    if os.path.exists(path):
                        size_before = get_dir_size(path)
                        shutil.rmtree(path, ignore_errors=True)
                        os.makedirs(path, exist_ok=True)
                        size_after = get_dir_size(path)
                        freed = size_before - size_after
                        total_size += freed
                        total_files += 1
                        details.append(f"清理微信缓存 {user}/{cd}，释放约 {freed / (1024*1024):.1f} MB")
        # QQ
        qq_path = os.path.expandvars(r'%USERPROFILE%\Documents\Tencent Files')
        if os.path.exists(qq_path):
            for user in os.listdir(qq_path):
                cache_dirs = ['Image', 'Video']
                for cd in cache_dirs:
                    path = os.path.join(qq_path, user, cd)
                    if os.path.exists(path):
                        size_before = get_dir_size(path)
                        shutil.rmtree(path, ignore_errors=True)
                        os.makedirs(path, exist_ok=True)
                        size_after = get_dir_size(path)
                        freed = size_before - size_after
                        total_size += freed
                        total_files += 1
                        details.append(f"清理QQ缓存 {user}/{cd}，释放约 {freed / (1024*1024):.1f} MB")

    # 系统临时文件
    if 'temp_files' in selected_ids:
        from tempfile import gettempdir
        temp_dirs = [gettempdir(), os.path.expandvars(r'%SystemRoot%\Temp')]
        for tmp in temp_dirs:
            if os.path.exists(tmp):
                for root, dirs, files in os.walk(tmp):
                    for f in files:
                        try:
                            fp = os.path.join(root, f)
                            s = os.path.getsize(fp)
                            os.remove(fp)
                            total_files += 1
                            total_size += s
                        except Exception:
                            pass
                details.append(f"清理临时文件夹 {tmp}")

    return {
        "total_files": total_files,
        "total_size_mb": round(total_size / (1024*1024), 2),
        "details": details
    }

# ================== 网络诊断 & 修复 ==================
@eel.expose
def diagnose_network():
    """网络诊断：检查连通性、DNS、代理等"""
    report = []
    status = "ok"

    # 1. 检查网络适配器状态
    try:
        result = subprocess.run(['ipconfig'], capture_output=True, text=True, timeout=5, creationflags=CREATE_NO_WINDOW)
        if 'Media disconnected' in result.stdout or '媒体已断开' in result.stdout:
            report.append("⚠️ 有网卡未连接（可能正常）。")
            status = "warning"
        else:
            report.append("✅ 网卡状态正常。")
    except Exception:
        report.append("❌ 无法获取网卡状态。")
        status = "error"

    # 2. Ping 百度检查连通性
    try:
        ping_result = subprocess.run(['ping', 'www.baidu.com', '-n', '2'], capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW)
        if 'TTL=' in ping_result.stdout:
            match = re.search(r'Average = (\d+)ms', ping_result.stdout)
            if match:
                report.append(f"✅ Ping 百度正常，平均延迟 {match.group(1)}ms。")
            else:
                report.append("✅ Ping 百度正常。")
        else:
            report.append("❌ 无法Ping通百度，请检查网络连接。")
            status = "error"
    except Exception:
        report.append("❌ Ping测试失败。")
        status = "error"

    # 3. DNS 解析测试
    try:
        socket.gethostbyname('www.baidu.com')
        report.append("✅ DNS解析正常。")
    except Exception:
        report.append("❌ DNS解析失败，可能DNS设置有问题。")
        status = "error"

    # 4. 检查代理设置
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings')
        proxy_enable, _ = winreg.QueryValueEx(key, 'ProxyEnable')
        if proxy_enable:
            report.append("⚠️ 系统代理已开启，可能影响网络。")
            status = "warning"
        else:
            report.append("✅ 系统代理未开启。")
        winreg.CloseKey(key)
    except Exception:
        report.append("⚠️ 无法检查代理设置。")

    return {"status": status, "report": "\n".join(report)}

@eel.expose
def fix_network():
    """一键修复常见网络问题（需要管理员权限）"""
    fixes = []
    try:
        subprocess.run(['ipconfig', '/release'], capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW)
        subprocess.run(['ipconfig', '/renew'], capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        fixes.append("✅ 已释放并更新IP地址。")
    except Exception:
        fixes.append("❌ IP更新失败，请手动操作。")

    try:
        subprocess.run(['ipconfig', '/flushdns'], capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW)
        fixes.append("✅ 已刷新DNS缓存。")
    except Exception:
        fixes.append("❌ DNS刷新失败。")

    try:
        subprocess.run(['netsh', 'winsock', 'reset'], capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW)
        fixes.append("✅ 已重置Winsock目录。")
    except Exception:
        fixes.append("❌ Winsock重置失败。")

    try:
        subprocess.run(['netsh', 'int', 'ip', 'reset'], capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW)
        fixes.append("✅ 已重置TCP/IP协议栈。")
    except Exception:
        fixes.append("❌ TCP/IP重置失败。")

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings', 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, 'ProxyEnable', 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)
        fixes.append("✅ 已关闭系统代理。")
    except Exception:
        fixes.append("⚠️ 无法自动关闭代理，请手动检查。")

    fixes.append("🔔 部分修复需要重启电脑才能生效。")
    return "\n".join(fixes)

@eel.expose
def network_speed_test():
    """简易网络测速：下载+上传测试"""
    result_parts = []

    # ---------- 下载测试 ----------
    test_url = "http://speedtest.tele2.net/1MB.zip"
    try:
        start = time.time()
        with urllib.request.urlopen(test_url, timeout=15) as f:
            data = f.read()
        elapsed = time.time() - start
        size_mb = len(data) / (1024 * 1024)
        speed_mbps = (size_mb * 8) / elapsed
        result_parts.append(f"⬇️ 下载速度：{speed_mbps:.2f} Mbps（{size_mb:.2f} MB 用时 {elapsed:.2f} 秒）")
    except Exception as e:
        result_parts.append(f"⬇️ 下载测速失败：{e}")

    # ---------- 上传测试 ----------
    upload_url = "http://httpbin.org/post"
    upload_data = bytearray(1024 * 1024)
    for i in range(0, len(upload_data), 4):
        upload_data[i:i+4] = os.urandom(4)
    try:
        start = time.time()
        req = urllib.request.Request(upload_url, data=upload_data, method='POST')
        with urllib.request.urlopen(req, timeout=15) as f:
            f.read()
        elapsed = time.time() - start
        size_mb = len(upload_data) / (1024 * 1024)
        speed_mbps = (size_mb * 8) / elapsed
        result_parts.append(f"⬆️ 上传速度：{speed_mbps:.2f} Mbps（{size_mb:.2f} MB 用时 {elapsed:.2f} 秒）")
    except Exception as e:
        result_parts.append(f"⬆️ 上传测速失败：{e} （可尝试使用 speedtest.net 测试完整速度）")

    return "\n".join(result_parts)

# ================== 右键菜单管理 ==================

def _copy_key(src_hive, src_path, dst_hive, dst_path, _depth=0):
    """递归复制注册表键和值"""
    MAX_DEPTH = 20
    MAX_ENUM = 5000
    if _depth > MAX_DEPTH:
        return
    src_key = winreg.OpenKey(src_hive, src_path, 0, winreg.KEY_READ)
    dst_key = winreg.CreateKey(dst_hive, dst_path)
    i = 0
    while i < MAX_ENUM:
        try:
            name, data, type_ = winreg.EnumValue(src_key, i)
            winreg.SetValueEx(dst_key, name, 0, type_, data)
            i += 1
        except OSError:
            break
        except Exception:
            i += 1  # 跳过异常值继续
    j = 0
    while j < MAX_ENUM:
        try:
            sub_name = winreg.EnumKey(src_key, j)
            _copy_key(src_hive, f"{src_path}\\{sub_name}", dst_hive, f"{dst_path}\\{sub_name}", _depth + 1)
            j += 1
        except OSError:
            break
        except Exception:
            j += 1
    winreg.CloseKey(src_key)
    winreg.CloseKey(dst_key)

def _delete_key(hive, path, _depth=0):
    """递归删除注册表键"""
    MAX_DEPTH = 20
    MAX_ENUM = 5000
    if _depth > MAX_DEPTH:
        return
    key = winreg.OpenKey(hive, path, 0, winreg.KEY_ALL_ACCESS)
    count = 0
    while count < MAX_ENUM:
        try:
            sub = winreg.EnumKey(key, 0)
            _delete_key(hive, f"{path}\\{sub}", _depth + 1)
            count += 1
        except OSError:
            break
        except Exception:
            count += 1
    winreg.CloseKey(key)
    winreg.DeleteKey(hive, path)

@eel.expose
def get_context_menu_items():
    """获取常用右键菜单项列表（文件、文件夹、桌面背景、驱动器）"""
    items = []
    scan_paths = [
        (winreg.HKEY_CLASSES_ROOT, r"*\shell"),
        (winreg.HKEY_CLASSES_ROOT, r"Directory\shell"),
        (winreg.HKEY_CLASSES_ROOT, r"Directory\Background\shell"),
        (winreg.HKEY_CLASSES_ROOT, r"Drive\shell"),
    ]
    for hive, sub_key in scan_paths:
        try:
            key = winreg.OpenKey(hive, sub_key, 0, winreg.KEY_READ)
            i = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(key, i)
                    full_path = f"{sub_key}\\{sub_name}"
                    if sub_name.endswith("_disabled_by_pcdoctor"):
                        i += 1
                        continue
                    try:
                        sub = winreg.OpenKey(hive, full_path, 0, winreg.KEY_READ)
                        display_name, _ = winreg.QueryValueEx(sub, "")
                        if not display_name:
                            display_name = sub_name
                        winreg.CloseKey(sub)
                    except Exception:
                        display_name = sub_name

                    command = ""
                    try:
                        cmd_key = winreg.OpenKey(hive, full_path + r"\command", 0, winreg.KEY_READ)
                        command, _ = winreg.QueryValueEx(cmd_key, "")
                        winreg.CloseKey(cmd_key)
                    except Exception:
                        pass

                    is_system = False
                    if command:
                        cmd_lower = command.lower()
                        if "system32" in cmd_lower or "syswow64" in cmd_lower:
                            is_system = True
                    if sub_name.lower() in ["open", "edit", "print", "printto", "runas", "find"]:
                        is_system = True

                    items.append({
                        "id": full_path.replace("\\", "/"),
                        "hive": "HKEY_CLASSES_ROOT",
                        "path": full_path,
                        "name": display_name,
                        "command": command,
                        "is_system": is_system,
                        "enabled": True
                    })
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except Exception:
            pass
    return items

@eel.expose
def disable_context_menu_item(path):
    """禁用右键菜单项：备份后删除原键"""
    try:
        hive = winreg.HKEY_CLASSES_ROOT
        backup_hive = winreg.HKEY_CURRENT_USER
        backup_root = r"Software\PCDoctor\ContextMenuBackup"
        backup_path = f"{backup_root}\\{path}"
        try:
            backup_key = winreg.CreateKey(backup_hive, backup_path)
            _copy_key(hive, path, backup_hive, backup_path)
            winreg.CloseKey(backup_key)
        except Exception:
            pass
        _delete_key(hive, path)
        return {"success": True, "message": f"已禁用 {os.path.basename(path)}"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@eel.expose
def enable_context_menu_item(path):
    """启用右键菜单项：从备份恢复"""
    try:
        hive = winreg.HKEY_CLASSES_ROOT
        backup_hive = winreg.HKEY_CURRENT_USER
        backup_root = r"Software\PCDoctor\ContextMenuBackup"
        backup_path = f"{backup_root}\\{path}"
        backup_key = winreg.OpenKey(backup_hive, backup_path, 0, winreg.KEY_READ)
        winreg.CloseKey(backup_key)
        _copy_key(backup_hive, backup_path, hive, path)
        _delete_key(backup_hive, backup_path)
        return {"success": True, "message": f"已启用 {os.path.basename(path)}"}
    except Exception as e:
        return {"success": False, "message": f"未找到备份，可能已被手动恢复: {e}"}

# ================== 自启动管理 ==================

# 备份目录
BACKUP_REG_PATH = r"Software\PCDoctor\StartupBackup"
BACKUP_FOLDER = os.path.join(os.getenv('APPDATA'), 'PCDoctor', 'StartupBackup')
if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER, exist_ok=True)

@eel.expose
def get_startup_items_full():
    """获取所有启动项详情（包括已禁用的项）"""
    items = []
    # 注册表扫描路径 (hive, key_path, is_hklm)
    reg_locations = [
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", True),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", True),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", False),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", False),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", True),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce", True),
    ]
    
    for hive, sub_key, is_hklm in reg_locations:
        try:
            key = winreg.OpenKey(hive, sub_key, 0, winreg.KEY_READ)
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    items.append({
                        "name": name,
                        "command": value,
                        "source": "registry",
                        "location": f"HKEY_{'LOCAL_MACHINE' if is_hklm else 'CURRENT_USER'}\\{sub_key}\\{name}",
                        "enabled": True,
                        "is_hklm": is_hklm,
                        "reg_path": sub_key,
                        "value_name": name,
                        "value_data": value
                    })
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except Exception:
            pass

    # 扫描启动文件夹
    startup_folders = [
        (os.path.expandvars(r'%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup'), False),
        (os.path.expandvars(r'%ProgramData%\Microsoft\Windows\Start Menu\Programs\Startup'), True),
    ]
    for folder, is_common in startup_folders:
        if os.path.exists(folder):
            for item in os.listdir(folder):
                if item.endswith('.lnk'):
                    full_path = os.path.join(folder, item)
                    items.append({
                        "name": item[:-4],
                        "command": full_path,
                        "source": "folder",
                        "location": full_path,
                        "enabled": True,
                        "folder_path": full_path
                    })

    # 读取备份的禁用注册表项
    try:
        backup_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, BACKUP_REG_PATH, 0, winreg.KEY_READ)
        idx = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(backup_key, idx)
                sub_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{BACKUP_REG_PATH}\\{subkey_name}", 0, winreg.KEY_READ)
                try:
                    orig_location, _ = winreg.QueryValueEx(sub_key, "Location")
                except Exception:
                    orig_location = ""
                try:
                    orig_name, _ = winreg.QueryValueEx(sub_key, "ValueName")
                except Exception:
                    orig_name = subkey_name
                try:
                    orig_command, _ = winreg.QueryValueEx(sub_key, "Command")
                except Exception:
                    orig_command = ""
                items.append({
                    "name": orig_name,
                    "command": orig_command,
                    "source": "registry",
                    "location": orig_location,
                    "enabled": False,
                    "backup_subkey": subkey_name
                })
                winreg.CloseKey(sub_key)
                idx += 1
            except OSError:
                break
        winreg.CloseKey(backup_key)
    except Exception:
        pass

    # 备份文件夹中的禁用项
    if os.path.exists(BACKUP_FOLDER):
        for item in os.listdir(BACKUP_FOLDER):
            if item.endswith('.lnk'):
                full_path = os.path.join(BACKUP_FOLDER, item)
                items.append({
                    "name": item[:-4],
                    "command": full_path,
                    "source": "folder",
                    "location": full_path,
                    "enabled": False,
                    "folder_path": full_path
                })

    # 计算影响程度
    for item in items:
        exe_path = item.get('command', '')
        if item['source'] == 'registry':
            if exe_path.startswith('"'):
                end = exe_path.find('"', 1)
                if end != -1:
                    exe_path = exe_path[1:end]
            else:
                exe_path = exe_path.split(' ')[0]
        size_mb = 0
        if os.path.exists(exe_path):
            try:
                size_mb = round(os.path.getsize(exe_path) / (1024 * 1024), 2)
            except Exception:
                pass
        item['size_mb'] = size_mb
        if size_mb > 200:
            item['impact'] = '高'
        elif size_mb > 50:
            item['impact'] = '中'
        else:
            item['impact'] = '低'
        item['exe_path'] = exe_path

    return items

@eel.expose
def disable_startup_item(item_info):
    """禁用启动项：备份后删除"""
    try:
        if item_info['source'] == 'registry':
            hive = winreg.HKEY_LOCAL_MACHINE if item_info.get('is_hklm') else winreg.HKEY_CURRENT_USER
            sub_path = item_info['reg_path']
            value_name = item_info['value_name']
            key = winreg.OpenKey(hive, sub_path, 0, winreg.KEY_ALL_ACCESS)
            command, _ = winreg.QueryValueEx(key, value_name)
            # 备份
            backup_name = f"{sub_path.replace('\\', '_')}_{value_name}"
            backup_key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{BACKUP_REG_PATH}\\{backup_name}")
            winreg.SetValueEx(backup_key, "Location", 0, winreg.REG_SZ, item_info['location'])
            winreg.SetValueEx(backup_key, "ValueName", 0, winreg.REG_SZ, value_name)
            winreg.SetValueEx(backup_key, "Command", 0, winreg.REG_SZ, command)
            winreg.SetValueEx(backup_key, "Hive", 0, winreg.REG_SZ, "HKEY_LOCAL_MACHINE" if item_info.get('is_hklm') else "HKEY_CURRENT_USER")
            winreg.SetValueEx(backup_key, "KeyPath", 0, winreg.REG_SZ, sub_path)
            winreg.CloseKey(backup_key)
            winreg.DeleteValue(key, value_name)
            winreg.CloseKey(key)
        elif item_info['source'] == 'folder':
            src = item_info['folder_path']
            if os.path.exists(src):
                shutil.move(src, os.path.join(BACKUP_FOLDER, os.path.basename(src)))
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": str(e)}

@eel.expose
def enable_startup_item(item_info):
    """启用启动项：从备份恢复"""
    try:
        if item_info['source'] == 'registry':
            backup_name = item_info.get('backup_subkey')
            if not backup_name:
                return {"success": False, "message": "无备份信息"}
            backup_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{BACKUP_REG_PATH}\\{backup_name}", 0, winreg.KEY_READ)
            command, _ = winreg.QueryValueEx(backup_key, "Command")
            value_name, _ = winreg.QueryValueEx(backup_key, "ValueName")
            hive_str, _ = winreg.QueryValueEx(backup_key, "Hive")
            key_path, _ = winreg.QueryValueEx(backup_key, "KeyPath")
            winreg.CloseKey(backup_key)
            hive = winreg.HKEY_LOCAL_MACHINE if hive_str == "HKEY_LOCAL_MACHINE" else winreg.HKEY_CURRENT_USER
            key = winreg.OpenKey(hive, key_path, 0, winreg.KEY_ALL_ACCESS)
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)
            winreg.CloseKey(key)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{BACKUP_REG_PATH}\\{backup_name}")
        elif item_info['source'] == 'folder':
            # 文件夹备份恢复较复杂，需知道原位置。建议提示用户手动操作。
            return {"success": False, "message": "请手动将备份文件夹中的快捷方式移回原启动文件夹"}
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": str(e)}

@eel.expose
def locate_file(file_path):
    """打开文件所在目录并选中"""
    try:
        if os.path.exists(file_path):
            subprocess.Popen(['explorer', '/select,', file_path], creationflags=CREATE_NO_WINDOW)
        else:
            # 如果文件不存在，尝试打开所在目录
            parent = os.path.dirname(file_path)
            if os.path.exists(parent):
                subprocess.Popen(['explorer', parent], creationflags=CREATE_NO_WINDOW)
        return True
    except Exception:
        return False

@eel.expose
def search_online(name):
    """在线搜索启动项信息"""
    import webbrowser
    webbrowser.open(f"https://www.baidu.com/s?wd={name} 启动项 是否需要")
    return True

# ================== Hosts 文件管理 ==================

# Hosts 文件路径
HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
HOSTS_BACKUP_PATH = os.path.join(os.path.dirname(HOSTS_PATH), "hosts_backup_by_pcdoctor")

def _read_hosts_raw():
    """读取原始 Hosts 文件内容（列表形式）"""
    if not os.path.exists(HOSTS_PATH):
        return []
    try:
        with open(HOSTS_PATH, "r", encoding="utf-8") as f:
            return f.readlines()
    except (PermissionError, OSError) as e:
        raise PermissionError(f"无法读取 Hosts 文件（需要管理员权限）：{e}")

def _write_hosts_raw(lines):
    """写入 Hosts 文件"""
    # 先备份
    if not os.path.exists(HOSTS_BACKUP_PATH):
        shutil.copy2(HOSTS_PATH, HOSTS_BACKUP_PATH)
    with open(HOSTS_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)

@eel.expose
def get_hosts_rules():
    """获取 Hosts 规则列表，返回 [{ip, domain, enabled, raw_line}]"""
    rules = []
    try:
        lines = _read_hosts_raw()
    except Exception as e:
        return {"error": f"读取 Hosts 文件失败：{e}"}
    
    for line in lines:
        original = line.rstrip('\n')
        stripped = original.lstrip()
        # 跳过空行和纯注释行（没有 IP 的注释）
        if not stripped or stripped.startswith('#'):
            # 尝试解析注释行看是否包含有效规则
            content = stripped.lstrip('#').lstrip()
            parts = content.split()
            if len(parts) >= 2:
                # 这可能是一个被注释掉的规则
                ip = parts[0]
                domain = parts[1]
                # 简单判断 IP 格式
                if len(ip.split('.')) == 4:
                    rules.append({
                        "ip": ip,
                        "domain": domain,
                        "enabled": False,
                        "raw": original
                    })
                    continue
            # 否则视为普通注释
            rules.append({
                "ip": "",
                "domain": "",
                "enabled": False,
                "raw": original,
                "is_comment_only": True
            })
            continue
        
        # 正常行：IP + 域名
        parts = stripped.split()
        if len(parts) >= 2:
            ip = parts[0]
            domain = parts[1]
            rules.append({
                "ip": ip,
                "domain": domain,
                "enabled": True,
                "raw": original
            })
        else:
            # 不识别的内容
            rules.append({
                "ip": "",
                "domain": "",
                "enabled": False,
                "raw": original,
                "is_other": True
            })
    return rules

@eel.expose
def save_hosts_rules(rules):
    """保存规则列表到 Hosts 文件。rules 格式与 get_hosts_rules 返回的一致"""
    lines = []
    for rule in rules:
        # 如果是纯注释行（无IP）或其它不识别内容，直接保留原样
        if rule.get("is_comment_only") or rule.get("is_other"):
            lines.append(rule["raw"] + "\n")
            continue
        
        # 如果 IP 或 domain 为空，则跳过（删除该条规则）
        if not rule.get("ip") or not rule.get("domain"):
            continue
        
        line = f"{rule['ip']} {rule['domain']}"
        if not rule.get("enabled", True):
            line = "# " + line
        lines.append(line + "\n")
    
    try:
        _write_hosts_raw(lines)
        return {"success": True}
    except PermissionError:
        return {"success": False, "message": "权限不足，请以管理员身份运行程序。"}
    except Exception as e:
        return {"success": False, "message": f"保存失败：{e}"}

@eel.expose
def restore_hosts_backup():
    """从备份恢复 Hosts 文件"""
    if not os.path.exists(HOSTS_BACKUP_PATH):
        return {"success": False, "message": "备份文件不存在。"}
    try:
        shutil.copy2(HOSTS_BACKUP_PATH, HOSTS_PATH)
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"恢复失败：{e}"}

# 常用屏蔽规则（广告/恶意网站示例，可根据需要扩充）
PRESET_RULES = [
    ("127.0.0.1", "doubleclick.net"),
    ("127.0.0.1", "googlesyndication.com"),
    ("127.0.0.1", "googleadservices.com"),
    ("127.0.0.1", "adservice.google.com"),
]

@eel.expose
def get_preset_rules():
    """返回常用的屏蔽规则列表"""
    return [{"ip": ip, "domain": domain, "enabled": True, "raw": f"{ip} {domain}"} for ip, domain in PRESET_RULES]

@eel.expose
def add_hosts_entry(ip, domain):
    """添加一条主机记录"""
    try:
        with open(HOSTS_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n{ip} {domain}")
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": str(e)}

# ==================== 设置面板相关 ====================

# 当前版本号（每次发版时手动更新）
APP_VERSION = "1.7.0"
GITHUB_REPO = "mhh190601/PC-Doctor"

# 子工具下载配置（文件名被 GitHub 截断，原名为 中文名_vX.X.exe）
# download_url 默认指向 GitHub Release；若本地仓库内置了 dist 资源，则优先从内置资源提取（离线可用）
GITHUB_RELEASE_BASE = "https://github.com/mhh190601/PC-Doctor/releases/download"

# 子工具注册表：name 为内部键，展示名在软件中心前端定义
SUBTOOLS = {
    "cdisksaver": {
        "filename": "C._v2.0.exe",          # 已被 GitHub 截断后的文件名
        "release_tag": "cdisk-v1.0.0",
        "release_file": "C._v2.0.exe",
        "desc": "C盘救星 - 磁盘清理与空间分析",
    },
    "privacy_cleaner": {
        "filename": "隐私清理.exe",
        "release_tag": "tools-v1.0.0",
        "release_file": "隐私清理.exe",
        "desc": "隐私清理 - 清理浏览器缓存与历史记录",
    },
    "startup_manager": {
        "filename": "启动项管理.exe",
        "release_tag": "tools-v1.0.0",
        "release_file": "启动项管理.exe",
        "desc": "启动项管理 - 管理开机自启动程序",
    },
    "file_shredder": {
        "filename": "文件粉碎机.exe",
        "release_tag": "tools-v1.0.0",
        "release_file": "文件粉碎机.exe",
        "desc": "文件粉碎机 - 彻底删除文件不可恢复",
    },
}

# 内置 dist 资源目录（与 main.py 同级的 tools/dist），离线安装用
_BUILTIN_DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "dist")

# 下载进度（跨线程共享，前端轮询读取）
import threading
_download_lock = threading.Lock()
_download_progress = 0
_download_total = 0
_download_error = None   # 下载错误信息，None 表示正常
_download_current = ""   # 当前正在下载的子工具键


# ============================================================
# 子程序管理（通用下载安装，复用 C盘救星 直链下载框架）
# ============================================================

@eel.expose
def check_network():
    """检测是否能连接 GitHub（子工具下载源），区分'与 GitHub 连接失败'和'证书问题'"""
    try:
        urllib.request.urlopen('https://github.com', timeout=5)
        return True, None
    except urllib.error.URLError as e:
        # 证书错误：能建立 TCP 但 TLS 握手失败
        if isinstance(getattr(e, 'reason', None), ssl.SSLError) or \
           'CERT' in str(getattr(e, 'reason', '')).upper():
            return False, "与 GitHub 之间连接失败：SSL 证书验证错误，请检查本机根证书或网络代理设置"
        return False, "与 GitHub 之间连接失败，请检查网络连接"
    except Exception as e:
        return False, f"与 GitHub 之间连接失败：{str(e)[:80]}"


def get_tools_dir():
    """获取子程序存放目录：%APPDATA%/电脑医生/tools"""
    return os.path.join(os.getenv('APPDATA'), '电脑医生', 'tools')


def _subtool_target(subkey):
    """返回某子工具在用户工具目录中的目标路径"""
    cfg = SUBTOOLS[subkey]
    return os.path.join(get_tools_dir(), cfg["filename"])


def _builtin_dist_path(subkey):
    """若仓库内置了 dist 资源则返回路径，否则 None"""
    cfg = SUBTOOLS[subkey]
    p = os.path.join(_BUILTIN_DIST_DIR, cfg["filename"])
    return p if os.path.exists(p) else None


@eel.expose
def is_subtool_installed(subkey):
    """检查某子工具是否已安装（通用）"""
    if subkey not in SUBTOOLS:
        return False
    return os.path.exists(_subtool_target(subkey))


@eel.expose
def list_subtools_status():
    """返回所有子工具的安装状态，供前端初始化卡片"""
    return {k: os.path.exists(_subtool_target(k)) for k in SUBTOOLS}


def _install_from_builtin(subkey):
    """从内置 dist 资源复制到用户工具目录（离线安装），返回 (success, message)"""
    src = _builtin_dist_path(subkey)
    if not src:
        return False, "未找到内置安装包（开发模式需先执行 tools/build_subtools.py 打包）"
    target = _subtool_target(subkey)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copy2(src, target)
    return True, f"已从本地资源安装 {SUBTOOLS[subkey]['filename']}"


@eel.expose
def install_subtool(subkey):
    """通用子工具安装：优先内置 dist，否则从 GitHub Release 下载（含实时进度回调）"""
    global _download_progress, _download_total, _download_error, _download_current

    if subkey not in SUBTOOLS:
        return {"success": False, "message": f"未知子工具：{subkey}"}

    # 1. 若已安装，直接返回
    target = _subtool_target(subkey)
    if os.path.exists(target):
        with _download_lock:
            _download_error = None
        return {"success": True, "message": "已安装，可直接打开"}

    # 2. 优先使用内置 dist 资源（离线可用，验收友好）
    builtin = _builtin_dist_path(subkey)
    if builtin:
        try:
            ok, msg = _install_from_builtin(subkey)
            with _download_lock:
                _download_progress = os.path.getsize(builtin)
                _download_total = os.path.getsize(builtin)
                _download_error = None
                _download_current = subkey
            return {"success": ok, "message": msg, "local": True}
        except Exception as e:
            return {"success": False, "message": f"本地安装失败：{str(e)[:100]}"}

    # 3. 否则从 GitHub Release 直链下载
    cfg = SUBTOOLS[subkey]
    url = f"{GITHUB_RELEASE_BASE}/{cfg['release_tag']}/{cfg['release_file']}"

    net_ok, net_msg = check_network()
    if not net_ok:
        with _download_lock:
            _download_error = net_msg
            _download_current = subkey
        return {"success": False, "offline": True, "message": net_msg}

    tools_dir = get_tools_dir()
    os.makedirs(tools_dir, exist_ok=True)

    with _download_lock:
        _download_progress = 0
        _download_total = 0
        _download_error = None
        _download_current = subkey

    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        with _download_lock:
            _download_total = int(response.headers.get('content-length', 0))
            _download_progress = 0

        temp_file = target + '.tmp'
        with open(temp_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    with _download_lock:
                        _download_progress += len(chunk)
        os.replace(temp_file, target)
        with _download_lock:
            downloaded = _download_progress
            total = _download_total
            _download_error = None
        print(f"[子工具] {subkey} 下载完成：{downloaded}/{total} 字节", flush=True)
        return {"success": True, "message": f"下载完成，{cfg['filename']} 已安装"}
    except requests.exceptions.SSLError:
        error_msg = "与 GitHub 之间连接失败：SSL 证书验证错误，请检查本机根证书或网络代理设置"
        with _download_lock:
            _download_error = error_msg
        return {"success": False, "message": error_msg}
    except requests.exceptions.HTTPError:
        if response.status_code == 404:
            error_msg = f"安装包不存在（404）：GitHub Release 未找到 {cfg['filename']}，请联系开发者确认"
        else:
            error_msg = f"下载失败：服务器返回 {response.status_code}"
        with _download_lock:
            _download_error = error_msg
        return {"success": False, "message": error_msg}
    except requests.exceptions.ConnectionError:
        error_msg = "与 GitHub 之间连接失败，请检查网络连接"
        with _download_lock:
            _download_error = error_msg
        return {"success": False, "message": error_msg}
    except requests.exceptions.Timeout:
        error_msg = "下载超时，请稍后重试"
        with _download_lock:
            _download_error = error_msg
        return {"success": False, "message": error_msg}
    except Exception as e:
        error_msg = f"下载失败：{str(e)[:100]}"
        with _download_lock:
            _download_error = error_msg
        for f in [target, target + '.tmp']:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
        return {"success": False, "message": error_msg}


@eel.expose
def get_download_progress():
    """返回当前下载进度（前端轮询调用），含错误状态与当前子工具键"""
    with _download_lock:
        downloaded = _download_progress
        total = _download_total
        error = _download_error
        current = _download_current
    if error:
        return {"percent": 0, "downloaded": 0, "total": 0, "error": True, "message": error, "subkey": current}
    if total > 0:
        percent = round((downloaded / total) * 100, 1)
        return {"percent": percent, "downloaded": downloaded, "total": total, "error": False, "subkey": current}
    return {"percent": 0, "downloaded": 0, "total": 0, "error": False, "subkey": current}


@eel.expose
def launch_subtool(subkey):
    """通用子工具启动：查找用户工具目录中的 exe 并打开"""
    if subkey not in SUBTOOLS:
        return {"success": False, "message": f"未知子工具：{subkey}"}
    target = _subtool_target(subkey)
    print(f"[子工具] {subkey} 目标: {target}", flush=True)
    print(f"[子工具] {subkey} 存在: {os.path.exists(target)}", flush=True)
    if not os.path.exists(target):
        return {"success": False, "message": "子工具未安装，请先下载"}

    # 方法1: os.startfile（Windows原生，最可靠）
    try:
        os.startfile(target)
        return {"success": True, "message": f"{SUBTOOLS[subkey]['filename']} 已启动"}
    except Exception as e:
        print(f"[子工具] {subkey} os.startfile 失败: {e}", flush=True)
    # 方法2: subprocess.Popen（不带 shell，list 方式）
    try:
        subprocess.Popen([target], cwd=os.path.dirname(target))
        return {"success": True, "message": f"{SUBTOOLS[subkey]['filename']} 已启动"}
    except Exception as e2:
        print(f"[子工具] {subkey} Popen(list) 失败: {e2}", flush=True)
    # 方法3: subprocess.Popen（带 shell）
    try:
        subprocess.Popen(f'"{target}"', shell=True, cwd=os.path.dirname(target))
        return {"success": True, "message": f"{SUBTOOLS[subkey]['filename']} 已启动"}
    except Exception as e3:
        print(f"[子工具] {subkey} Popen(shell) 失败: {e3}", flush=True)
        return {"success": False, "message": f"启动失败：{e3}"}


# ========== 以下为 C盘救星 兼容别名（委托给通用实现）==========

@eel.expose
def is_cdisksaver_installed():
    """[兼容] 检查 C盘救星 是否安装"""
    return is_subtool_installed("cdisksaver")


@eel.expose
def install_cdisksaver():
    """[兼容] 安装 C盘救星"""
    return install_subtool("cdisksaver")


@eel.expose
def launch_cdisksaver():
    """[兼容] 启动 C盘救星"""
    return launch_subtool("cdisksaver")


@eel.expose
def get_app_info():
    """返回应用信息（版本、仓库地址等）"""
    return {
        "version": APP_VERSION,
        "repo": f"https://github.com/{GITHUB_REPO}",
        "author": "mhh190601",
        "name": "电脑医生 (PC Doctor)"
    }

@eel.expose
def check_for_update():
    """检查 GitHub Release 是否有新版本"""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "PC-Doctor-Update-Check")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            latest_version = data.get("tag_name", "").lstrip("v")
            download_url = data.get("html_url", "")
            
            if latest_version and latest_version != APP_VERSION:
                try:
                    latest_parts = [int(x) for x in latest_version.split(".")]
                    current_parts = [int(x) for x in APP_VERSION.split(".")]
                    if latest_parts > current_parts:
                        return {
                            "has_update": True,
                            "latest_version": latest_version,
                            "current_version": APP_VERSION,
                            "download_url": download_url
                        }
                except Exception:
                    if latest_version != APP_VERSION:
                        return {
                            "has_update": True,
                            "latest_version": latest_version,
                            "current_version": APP_VERSION,
                            "download_url": download_url
                        }
            return {"has_update": False, "current_version": APP_VERSION}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"has_update": False, "error": "仓库暂不可用，请稍后再试。", "current_version": APP_VERSION}
        return {"has_update": False, "error": f"网络错误：{e.code}", "current_version": APP_VERSION}
    except Exception as e:
        return {"has_update": False, "error": f"检查失败：{str(e)[:50]}", "current_version": APP_VERSION}

@eel.expose
def get_autostart_status():
    """检查当前是否已设置开机自启"""
    startup_folder = os.path.expandvars(r'%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup')
    shortcut_path = os.path.join(startup_folder, "电脑医生.lnk")
    return {"enabled": os.path.exists(shortcut_path)}

@eel.expose
def set_autostart(enabled):
    """设置或取消开机自启"""
    startup_folder = os.path.expandvars(r'%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup')
    if not os.path.exists(startup_folder):
        os.makedirs(startup_folder, exist_ok=True)
    shortcut_path = os.path.join(startup_folder, "电脑医生.lnk")
    
    if enabled:
        if getattr(sys, 'frozen', False):
            exe_path = sys.executable
        else:
            return {"success": False, "message": "源码运行无法设置开机自启，请使用打包后的 exe 版本。"}
        
        try:
            ps_script = f"""
            $WshShell = New-Object -ComObject WScript.Shell
            $Shortcut = $WshShell.CreateShortcut('{shortcut_path}')
            $Shortcut.TargetPath = '{exe_path}'
            $Shortcut.WorkingDirectory = '{os.path.dirname(exe_path)}'
            $Shortcut.Save()
            """
            subprocess.run(["powershell", "-Command", ps_script], capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
            return {"success": True, "message": "已设置开机自启"}
        except Exception as e:
            return {"success": False, "message": str(e)}
    else:
        if os.path.exists(shortcut_path):
            try:
                os.remove(shortcut_path)
                return {"success": True, "message": "已取消开机自启"}
            except Exception as e:
                return {"success": False, "message": str(e)}
        return {"success": True, "message": "未设置开机自启"}

@eel.expose
def export_knowledge_base():
    """导出知识库为 JSON 字符串"""
    try:
        with open(KB_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return "[]"

@eel.expose
def import_knowledge_base(json_str):
    """导入知识库 JSON 字符串，合并到现有知识库"""
    try:
        new_data = json.loads(json_str)
        if not isinstance(new_data, list):
            return {"success": False, "message": "格式错误，需要 JSON 数组"}
        
        existing = []
        if os.path.exists(KB_PATH):
            with open(KB_PATH, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        
        existing_questions = {item['question'] for item in existing}
        added = 0
        for item in new_data:
            if item.get('question') and item.get('answer'):
                if item['question'] not in existing_questions:
                    existing.append(item)
                    existing_questions.add(item['question'])
                    added += 1
        
        with open(KB_PATH, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=4)
        
        learning.refresh_cache()
        
        return {"success": True, "message": f"成功导入 {added} 条新知识"}
    except json.JSONDecodeError:
        return {"success": False, "message": "JSON 格式解析失败"}
    except Exception as e:
        return {"success": False, "message": str(e)}

# ================== 大文件扫描 & 开机耗时 ==================
@eel.expose
def scan_large_files(drive="C:", min_size_mb=50):
    """扫描指定磁盘的大文件（修复跳过软件目录的问题）"""
    results = []
    min_size = min_size_mb * 1024 * 1024
    
    # 确保盘符格式正确
    if not drive.endswith(':\\') and not drive.endswith(':/'):
        drive = drive.rstrip(':') + ':\\'
    if not os.path.exists(drive):
        return {"error": f"盘符 {drive} 不存在，请检查后重试。"}

    scanned_count = 0
    # 只跳过明确会导致权限问题或无限循环的系统目录
    skip_dirs = {
        '$Recycle.Bin',
        'System Volume Information',
        'Recovery',
        'Config.Msi'
    }
    
    try:
        for root, dirs, files in os.walk(drive):
            # 过滤掉系统隐藏目录，但保留 Program Files 等软件目录
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('$')]
            
            for file in files:
                scanned_count += 1
                if scanned_count % 1000 == 0:
                    print(f'[扫描进度] 已扫描 {scanned_count} 个文件...')
                    
                file_path = os.path.join(root, file)
                try:
                    size = os.path.getsize(file_path)
                    if size >= min_size:
                        results.append({
                            'path': file_path,
                            'size_mb': round(size / (1024 * 1024), 2),
                            'name': file
                        })
                except (OSError, PermissionError):
                    continue
    except Exception as e:
        print(f'扫描错误: {str(e)}')
        return {"error": str(e)}
    
    results.sort(key=lambda x: x['size_mb'], reverse=True)
    print(f'[扫描完成] 共扫描 {scanned_count} 个文件，找到 {len(results)} 个大文件。')
    return results[:100]

# ================== C盘救星面板接口 ==================
@eel.expose
def get_c_disk_info():
    """获取C盘空间信息"""
    try:
        usage = psutil.disk_usage('C:\\')
        return {
            'total_gb': round(usage.total / (1024**3), 1),
            'used_gb': round(usage.used / (1024**3), 1),
            'free_gb': round(usage.free / (1024**3), 1),
            'percent': usage.percent
        }
    except Exception:
        return None


@eel.expose
def pick_folder():
    """弹出文件夹选择对话框，返回选中的文件夹路径"""
    import tkinter.filedialog as fd
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    folder = fd.askdirectory(title="选择目标文件夹")
    root.destroy()
    if folder:
        return {"folder": folder}
    return {"folder": None}

@eel.expose
def get_top_folders(drive="C:\\"):
    """获取C盘根目录下最大的文件夹"""
    results = []
    skip = {'Windows', '$Recycle.Bin', 'System Volume Information', 'Recovery'}
    try:
        for entry in os.scandir(drive):
            if entry.name in skip or not entry.is_dir():
                continue
            size = get_folder_size(entry.path)
            results.append({
                'name': entry.name,
                'size_mb': round(size / (1024 * 1024), 1)
            })
    except Exception:
        pass
    results.sort(key=lambda x: x['size_mb'], reverse=True)
    return results[:10]


@eel.expose
def get_boot_info():
    """获取本次开机时长和上次开机耗时"""
    info = {}
    
    # 1. 本次已运行时间（使用psutil.boot_time）
    try:
        import time
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        info['uptime'] = f"{int(hours)}小时{int(minutes)}分钟{int(seconds)}秒"
    except Exception:
        info['uptime'] = "无法获取"
    
    # 2. 上次开机耗时（通过事件日志ID 100计算）
    try:
        # 使用PowerShell查询最近两次启动事件
        cmd = 'powershell -Command "Get-WinEvent -FilterHashtable @{LogName=\'System\'; ID=100} -MaxEvents 2 | Select-Object -ExpandProperty TimeCreated"'
        output = subprocess.check_output(cmd, shell=True, text=True, creationflags=CREATE_NO_WINDOW)
        times = output.strip().split('\n')
        if len(times) >= 2:
            # 解析时间字符串，计算差值
            from datetime import datetime
            fmt = "%m/%d/%Y %H:%M:%S"
            t1 = datetime.strptime(times[0].strip(), fmt)
            t2 = datetime.strptime(times[1].strip(), fmt)
            boot_duration = t1 - t2
            total_seconds = boot_duration.total_seconds()
            if total_seconds < 300:  # 小于5分钟通常表示快速启动或异常
                info['last_boot_time'] = "快速启动模式，开机耗时极短"
                info['last_boot_seconds'] = total_seconds
            else:
                mins, secs = divmod(total_seconds, 60)
                info['last_boot_time'] = f"{int(mins)}分钟{int(secs)}秒"
                info['last_boot_seconds'] = total_seconds
        else:
            info['last_boot_time'] = "数据不足，请重启后再试"
            info['last_boot_seconds'] = 0
    except Exception:
        info['last_boot_time'] = "无法获取（可能需要管理员权限）"
        info['last_boot_seconds'] = 0
    
    return info

# ================== 文件夹空间树形分析 ==================

@eel.expose
def scan_directory_tree(root_path="C:\\", max_depth=3, top_n=20):
    """带调试输出的目录树扫描"""
    print("\n=== 文件夹大小分析调试 ===")
    
    def get_size(path):
        total = 0
        try:
            if os.path.isfile(path):
                return os.path.getsize(path)
            elif os.path.isdir(path):
                try:
                    for entry in os.scandir(path):
                        try:
                            if entry.is_file(follow_symlinks=False):
                                total += entry.stat().st_size
                            elif entry.is_dir(follow_symlinks=False):
                                total += get_size(entry.path)
                        except (OSError, PermissionError):
                            continue
                except (OSError, PermissionError):
                    pass
        except (OSError, PermissionError):
            pass
        return total

    def build_tree(current_path, current_depth=0):
        items = []
        try:
            with os.scandir(current_path) as it:
                for entry in it:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        if entry.name in {'$Recycle.Bin', 'System Volume Information', 'Recovery', 'Config.Msi'}:
                            continue
                        if is_dir:
                            size = get_size(entry.path)
                        else:
                            try:
                                size = entry.stat().st_size
                            except OSError:
                                size = 0
                        items.append({
                            'name': entry.name,
                            'path': entry.path,
                            'is_dir': is_dir,
                            'size_mb': round(size / (1024 * 1024), 2),
                            'children': []
                        })
                    except (OSError, PermissionError):
                        continue
            
            items.sort(key=lambda x: x['size_mb'], reverse=True)
            items = items[:top_n]
            
            if current_depth < max_depth:
                for item in items:
                    if item['is_dir']:
                        item['children'] = build_tree(item['path'], current_depth + 1)
            
            return items
        except (OSError, PermissionError):
            return []

    if not os.path.exists(root_path):
        print("路径不存在")
        return {"error": f"路径 {root_path} 不存在"}

    root_name = os.path.basename(root_path.rstrip('\\/')) or root_path
    total_size = get_size(root_path)
    print(f"根目录: {root_path} , 总大小: {round(total_size / (1024*1024*1024), 2)} GB ({round(total_size/(1024*1024), 1)} MB)")

    # 扫描顶层目录，逐一打印大小
    try:
        with os.scandir(root_path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        sz = get_size(entry.path)
                        print(f"  {entry.name}: {round(sz/(1024*1024), 1)} MB ({round(sz/(1024*1024*1024), 2)} GB)")
                except Exception:
                    pass
    except Exception as e:
        print(f"扫描顶层出错: {e}")

    tree = {
        'name': root_name,
        'path': root_path,
        'size_mb': round(total_size / (1024 * 1024), 2),
        'is_dir': True,
        'children': build_tree(root_path, 0)
    }
    print("=== 调试输出结束 ===\n")
    return tree

# ================== 系统信息概览 ==================
import platform

@eel.expose
def get_system_info():
    import subprocess
    info = {}
    info['os'] = f"{platform.system()} {platform.release()} ({platform.version()})"
    info['hostname'] = platform.node()

    # CPU 友好名称
    try:
        raw_cpu = platform.processor()
        # 尝试通过 wmic 获取更友好的名称
        result = subprocess.run(
            ['wmic', 'cpu', 'get', 'Name'],
            capture_output=True, text=True, timeout=5, creationflags=CREATE_NO_WINDOW
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(lines) > 1:
            info['cpu'] = lines[1]  # 通常第二行是CPU名称
        else:
            info['cpu'] = raw_cpu if raw_cpu else "无法识别"
    except Exception:
        info['cpu'] = platform.processor() or "无法识别"

    # 内存
    try:
        mem = psutil.virtual_memory()
        info['memory_total'] = round(mem.total / (1024**3), 1)
    except Exception:
        info['memory_total'] = 0

    # 显卡：优先取独显，排除虚拟设备
    try:
        result = subprocess.run(
            ['wmic', 'path', 'win32_videocontroller', 'get', 'name'],
            capture_output=True, text=True, timeout=5, creationflags=CREATE_NO_WINDOW
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        # 过滤掉虚拟设备、微软基本显示适配器
        gpu_list = [l for l in lines[1:] if l and 'GameViewer' not in l and 'Virtual' not in l and 'Microsoft Basic' not in l]
        
        if gpu_list:
            # 优先找独显（NVIDIA/AMD）
            dedicated = [g for g in gpu_list if 'NVIDIA' in g or 'AMD' in g or 'Radeon' in g]
            if dedicated:
                info['gpu'] = ', '.join(dedicated)  # 如果有多个独显，都显示
            else:
                info['gpu'] = gpu_list[0]  # 只有集显就显示集显
        else:
            info['gpu'] = lines[1] if len(lines) > 1 else "未检测到"
    except Exception:
        info['gpu'] = "获取失败"

    # 主板型号
    try:
        result = subprocess.run(
            ['wmic', 'baseboard', 'get', 'product'],
            capture_output=True, text=True, timeout=5, creationflags=CREATE_NO_WINDOW
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        info['motherboard'] = lines[1] if len(lines) > 1 else "未知"
    except Exception:
        info['motherboard'] = "未知"

    # 磁盘信息
    disks = []
    for part in psutil.disk_partitions():
        try:
            # 跳过网络路径、CD-ROM、无法访问的分区
            if part.mountpoint.startswith('\\\\') or 'cdrom' in part.opts or part.fstype == '':
                continue
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                'device': part.device,
                'total_gb': round(usage.total / (1024**3), 1),
                'used_gb': round(usage.used / (1024**3), 1),
                'free_gb': round(usage.free / (1024**3), 1)
            })
        except Exception:
            pass
    info['disks'] = disks
    return info

# ================== 启动项排行分析 ==================

@eel.expose
def get_startup_ranking():
    """获取启动项及其影响评级，按文件大小降序排列"""
    import winreg
    startups = []

    # 1. 扫描注册表中的启动项
    registry_locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    ]

    for hive, key_path in registry_locations:
        try:
            key = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ)
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    startups.append((name, value))
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except Exception:
            pass

    # 2. 扫描启动文件夹中的快捷方式
    startup_folders = [
        os.path.expandvars(r'%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup'),
        os.path.expandvars(r'%ProgramData%\Microsoft\Windows\Start Menu\Programs\Startup'),
    ]
    for folder in startup_folders:
        if os.path.exists(folder):
            for item in os.listdir(folder):
                if item.lower().endswith('.lnk'):
                    startups.append((item[:-4], os.path.join(folder, item)))

    # 3. 分析每个启动项
    results = []
    for name, command in startups:
        # 提取可执行文件路径（处理带引号和参数的情况）
        exe_path = command.strip()
        if exe_path.startswith('"'):
            end = exe_path.find('"', 1)
            if end != -1:
                exe_path = exe_path[1:end]
        else:
            exe_path = exe_path.split(' ')[0]

        exe_path = os.path.expandvars(exe_path)  # 扩展环境变量
        size_mb = 0
        try:
            if os.path.isfile(exe_path):
                size_mb = round(os.path.getsize(exe_path) / (1024 * 1024), 2)
        except Exception:
            pass

        # 影响程度评级
        if size_mb > 200:
            impact = '高'
        elif size_mb > 50:
            impact = '中'
        else:
            impact = '低'

        results.append({
            'name': name,
            'command': command,
            'exe_path': exe_path,
            'size_mb': size_mb,
            'impact': impact
        })

    # 按文件大小降序排列
    results.sort(key=lambda x: x['size_mb'], reverse=True)
    return results

# ================== 软件卸载助手 ==================

# ================== 重复文件查找 ==================

def get_file_md5(file_path, chunk_size=8192):
    """计算文件的 MD5 值，用于判断文件内容是否相同"""
    md5 = hashlib.md5()
    try:
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                md5.update(chunk)
        return md5.hexdigest()
    except (OSError, PermissionError):
        return None

@eel.expose
def find_duplicate_files(drive="C:\\", min_size_mb=1):
    """
    扫描指定目录，找出内容完全相同的重复文件
    min_size_mb: 只扫描大于此大小的文件（避免扫描大量小文件）
    """
    min_size = min_size_mb * 1024 * 1024
    hash_map = {}  # {md5: [file_path1, file_path2, ...]}

    # 跳过的目录
    skip_dirs = {'$Recycle.Bin', 'System Volume Information', 'Recovery', 'Config.Msi', 'Windows'}

    scanned = 0
    for root, dirs, files in os.walk(drive):
        # 跳过系统目录
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        for file in files:
            scanned += 1
            if scanned % 500 == 0:
                print(f'[重复文件扫描] 已扫描 {scanned} 个文件...')

            file_path = os.path.join(root, file)

            # 跳过太小的文件
            try:
                size = os.path.getsize(file_path)
                if size < min_size:
                    continue
            except (OSError, PermissionError):
                continue

            # 计算 MD5
            md5 = get_file_md5(file_path)
            if md5 is None:
                continue

            if md5 in hash_map:
                hash_map[md5].append(file_path)
            else:
                hash_map[md5] = [file_path]

    # 只保留有重复的组（至少2个文件）
    duplicates = {h: paths for h, paths in hash_map.items() if len(paths) > 1}

    # 整理结果
    results = []
    for md5, paths in duplicates.items():
        try:
            size_mb = round(os.path.getsize(paths[0]) / (1024 * 1024), 2)
        except Exception:
            size_mb = 0

        # 计算这组重复文件浪费的空间（保留一份，其余的都是浪费）
        wasted_mb = round(size_mb * (len(paths) - 1), 2)

        results.append({
            'md5': md5,
            'size_mb': size_mb,
            'count': len(paths),
            'wasted_mb': wasted_mb,
            'files': paths
        })

    # 按浪费空间降序排列
    results.sort(key=lambda x: x['wasted_mb'], reverse=True)

    print(f'[重复文件扫描] 完成，共扫描 {scanned} 个文件，找到 {len(results)} 组重复')
    return results[:50]  # 最多返回50组

@eel.expose
def delete_selected_files(file_list):
    """删除指定的文件列表"""
    deleted = 0
    errors = []
    for f in file_list:
        try:
            os.remove(f)
            deleted += 1
        except Exception as e:
            errors.append(f"删除失败 {f}: {str(e)}")
    return {"deleted": deleted, "errors": errors}

@eel.expose
def pick_files_for_shred():
    """
    打开文件选择对话框，让用户选择要粉碎的文件
    返回: {"files": ["C:\\path\\to\\file1", ...]} 或 {"files": []}
    """
    import tkinter as tk
    from tkinter import filedialog
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        file_paths = filedialog.askopenfilenames(
            title='选择要粉碎的文件（不可恢复！）',
            filetypes=[('所有文件', '*.*')]
        )
        root.destroy()
        return {"files": list(file_paths)}
    except Exception as e:
        return {"files": [], "error": str(e)}


@eel.expose
def shred_files(file_paths, passes=3):
    """
    文件粉碎：覆写后删除
    file_paths: 文件路径列表
    passes: 覆写次数，默认3次
    返回: {success: True/False, message: str, shredded: int}
    """
    shredded = 0
    errors = []
    
    for path in file_paths:
        if not os.path.exists(path):
            errors.append(f"文件不存在: {path}")
            continue
            
        try:
            # 移除只读属性
            os.chmod(path, stat.S_IWRITE)
            
            file_size = os.path.getsize(path)
            
            # 多次覆写
            for _ in range(passes):
                with open(path, 'wb') as f:
                    # 第一次写0，第二次写1，第三次随机
                    if _ == 0:
                        f.write(b'\x00' * file_size)
                    elif _ == 1:
                        f.write(b'\xFF' * file_size)
                    else:
                        f.write(os.urandom(file_size))
                    f.flush()
                    os.fsync(f.fileno())
            
            # 重命名为随机名称，防止原文件名恢复
            rand_name = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
            rand_path = os.path.join(os.path.dirname(path), rand_name)
            os.rename(path, rand_path)
            
            # 删除
            os.remove(rand_path)
            shredded += 1
            
        except PermissionError:
            errors.append(f"权限不足: {path}，请以管理员身份运行。")
        except Exception as e:
            errors.append(f"粉碎失败 {path}: {str(e)}")
    
    message = f"成功粉碎 {shredded} 个文件。"
    if errors:
        message += "\n" + "\n".join(errors)
    
    return {
        "success": shredded > 0,
        "message": message,
        "shredded": shredded
    }

@eel.expose
def get_removable_drives():
    """
    获取所有可移动磁盘（U盘）列表
    返回: [{"drive": "D:", "label": "MyDrive", "size_gb": 32.0, "model": "SanDisk"}, ...]
    """
    drives = []
    try:
        result = subprocess.run(
            ['powershell', '-Command', 
             'Get-WmiObject -Class Win32_LogicalDisk | Where-Object { $_.DriveType -eq 2 } | ForEach-Object { Write-Output "$($_.DeviceID)|$($_.VolumeName)|$([math]::Round($_.Size/1GB,1))" }'],
            capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW
        )
        lines = result.stdout.strip().split('\n')
        for line in lines:
            if '|' in line:
                parts = line.strip().split('|')
                if len(parts) >= 3:
                    drives.append({
                        "drive": parts[0],
                        "label": parts[1] or "未命名",
                        "size_gb": float(parts[2])
                    })
    except Exception:
        pass
    return drives

@eel.expose
def write_iso_to_usb(iso_path, target_drive):
    """
    将ISO镜像写入U盘
    iso_path: ISO文件路径
    target_drive: 目标盘符，如 "D:"
    返回写入结果
    """
    if not os.path.exists(iso_path):
        return {"success": False, "message": "ISO文件不存在，请检查路径。"}
    
    if not target_drive:
        return {"success": False, "message": "请选择目标U盘。"}
    
    target_drive = target_drive.rstrip('\\').rstrip(':')
    
    try:
        ps_script = f"""
        $iso = "{iso_path}"
        $drive = "{target_drive}"
        $mount = Mount-DiskImage -ImagePath $iso -StorageType ISO -PassThru
        $driveLetter = ($mount | Get-Volume).DriveLetter
        $sourcePath = $driveLetter + ":\\"
        Format-Volume -DriveLetter $drive -FileSystem FAT32 -NewFileSystemLabel "PC-DOCTOR" -Force -Confirm:$false
        Copy-Item -Path "$sourcePath*" -Destination "$drive\\" -Recurse -Force
        Dismount-DiskImage -ImagePath $iso
        Write-Output "SUCCESS"
        """
        result = subprocess.run(
            ['powershell', '-Command', ps_script],
            capture_output=True, text=True, timeout=1800, creationflags=CREATE_NO_WINDOW
        )
        if "SUCCESS" in result.stdout:
            return {"success": True, "message": f"启动盘制作成功！\n已将ISO内容写入 {target_drive}: 盘。"}
        else:
            error_msg = result.stderr or result.stdout or "未知错误"
            return {"success": False, "message": f"写入失败：{error_msg[:200]}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "写入超时（超过30分钟），请检查ISO文件和U盘状态。"}
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}

# ================== 安全体检（Windows 安全中心集成）==================

def _run_ps(command: str, timeout: int = 8) -> str:
    """安全执行 PowerShell 命令，返回 stdout 文本"""
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', command],
            capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""

@eel.expose
def get_security_status():
    """获取 Windows 安全中心各项防护状态（纯读取，零风险）

    返回前端 renderSecurityStatus 期望的结构化格式：
    每项含 status(ok|warn|bad|unknown) 与 detail(描述文本)。
    检测失败（非 Windows / 无权限 / 超时）时该项为 unknown + "无法检测"，
    整体 offline=True，对应验收"离线时显示无法检测"。
    """
    def _item(status, detail):
        return {"status": status, "detail": detail}

    result = {
        "defender": _item("unknown", "无法检测"),
        "firewall": _item("unknown", "无法检测"),
        "smartscreen": _item("unknown", "无法检测"),
        "memory_integrity": _item("unknown", "无法检测"),
        "secure_boot": _item("unknown", "无法检测"),
        "threats": _item("unknown", "无法检测"),
        "overall_score": 0,   # 0-5
        "offline": False,
    }

    # 非 Windows 平台直接返回"无法检测"
    if os.name != "nt":
        result["offline"] = True
        return result

    # -- Defender / 实时保护 / 病毒库 / 上次扫描 --
    try:
        raw = _run_ps(
            '$s=Get-MpComputerStatus -ErrorAction SilentlyContinue;'
            'Write-Host $s.AntivirusEnabled;'
            'Write-Host $s.RealTimeProtectionEnabled;'
            'Write-Host $s.AntispywareSignatureVersion;'
            'Write-Host $s.AntispywareSignatureLastUpdated;'
            'Write-Host $s.QuickScanEndTime'
        )
        lines = [l.strip() for l in raw.split('\n') if l.strip()]
        if len(lines) >= 5:
            av_on = lines[0].lower() == "true"
            rtp = lines[1].lower() == "true"
            ver = lines[2]
            updated = lines[3]
            quick = lines[4]
            if av_on and rtp:
                dstatus = "ok"
            elif av_on or rtp:
                dstatus = "warn"
            else:
                dstatus = "bad"
            parts = []
            if ver:
                parts.append(f"病毒库版本 {ver}")
            if updated and not updated.lower().startswith("1/1/0001"):
                parts.append(f"更新于 {updated.split(' ')[0]}")
            if quick and not quick.lower().startswith("1/1/0001"):
                parts.append(f"上次扫描 {quick.split(' ')[0]}")
            elif not quick or quick.lower().startswith("1/1/0001"):
                parts.append("尚未扫描")
            result["defender"] = _item(dstatus, " | ".join(parts) if parts else "实时防病毒状态未知")
    except Exception:
        pass

    # -- 防火墙 --
    try:
        fw = _run_ps(
            '$p=Get-NetFirewallProfile -ErrorAction SilentlyContinue;'
            '(($p|ForEach-Object{$_.Enabled}) -join ",")'
        )
        on = "True" in fw
        result["firewall"] = _item("ok" if on else "bad",
                                   "防火墙已开启（域/专用/公用）" if on else "防火墙未完全开启")
    except Exception:
        pass

    # -- SmartScreen --
    try:
        ss = _run_ps(
            '$r=Get-ItemProperty -Path "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Explorer" '
            '-Name "SmartScreenEnabled" -ErrorAction SilentlyContinue;'
            'Write-Host $r.SmartScreenEnabled'
        )
        on = ss.strip().lower() in ("on", "requireadmin", "prompt")
        result["smartscreen"] = _item("ok" if on else "bad",
                                      f"SmartScreen: {ss.strip() or '未知'}")
    except Exception:
        pass

    # -- 内核隔离（内存完整性）--
    try:
        mi = _run_ps(
            '$d=Get-CimInstance -ClassName Win32_DeviceGuard '
            '-Namespace root\\Microsoft\\Windows\\DeviceGuard '
            '-ErrorAction SilentlyContinue;'
            'Write-Host $d.SecurityServicesRunning'
        )
        on = "HypervisorEnforcedCodeIntegrity" in mi
        result["memory_integrity"] = _item("ok" if on else "warn",
                                           "内核隔离（内存完整性）已开启" if on else "内核隔离（内存完整性）未开启")
    except Exception:
        pass

    # -- 安全启动 --
    try:
        sb = _run_ps('Confirm-SecureBootUEFI -ErrorAction SilentlyContinue; Write-Host $?')
        on = "True" in sb
        result["secure_boot"] = _item("ok" if on else "warn",
                                      "安全启动已启用（UEFI）" if on else "安全启动未启用")
    except Exception:
        pass

    # -- 威胁计数 --
    try:
        tc = _run_ps(
            'try { $t=Get-MpThreat -ErrorAction Stop; Write-Host $t.Count } catch { Write-Host 0 }'
        )
        count = int(tc) if tc.strip().isdigit() else 0
        if count == 0:
            result["threats"] = _item("ok", "未发现活动威胁")
        else:
            result["threats"] = _item("bad", f"检测到 {count} 个威胁")
    except Exception:
        pass

    # -- 评分（0-5）：ok 计 1 分，warn 计 0.5 分 --
    score = 0.0
    for k in ("defender", "firewall", "smartscreen", "memory_integrity", "secure_boot"):
        if result[k]["status"] == "ok":
            score += 1
        elif result[k]["status"] == "warn":
            score += 0.5
    if result["threats"]["status"] == "bad":
        score = max(0.0, score - 1)
    result["overall_score"] = int(round(score))

    # 若全部项均为 unknown，则视为无法检测（离线/无权限）
    if all(result[k]["status"] == "unknown" for k in
           ("defender", "firewall", "smartscreen", "memory_integrity", "secure_boot", "threats")):
        result["offline"] = True

    return result


@eel.expose
def launch_defender_scan(quick=True):
    """启动 Windows Defender 扫描（quick=True 快速扫描，False 全面扫描）"""
    try:
        scan_type = "QuickScan" if quick else "FullScan"
        subprocess.run(
            ['powershell', '-NoProfile', '-Command', f'Start-MpScan -ScanType {scan_type}'],
            capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW
        )
        return {"success": True,
                "message": "已启动 Defender 快速扫描" if quick else "已启动 Defender 全面扫描"}
    except Exception as e:
        return {"success": False, "message": f"启动扫描失败：{str(e)[:120]}"}


@eel.expose
def open_windows_security():
    """打开 Windows 安全中心（ms-settings 深度链接，失败回退 explorer）"""
    try:
        os.startfile("ms-settings:windowsdefender")
        return {"success": True, "message": "已打开 Windows 安全中心"}
    except Exception:
        pass
    try:
        subprocess.run(['explorer.exe', 'windowsdefender:'], creationflags=CREATE_NO_WINDOW)
        return {"success": True, "message": "已打开 Windows 安全中心"}
    except Exception as e:
        return {"success": False, "message": f"打开失败：{str(e)[:120]}"}

# ================== 启动 ==================
import atexit
import signal
import tkinter as tk

def show_splash(duration=2800):
    """透明背景 + Logo 从上到下高亮扫描 + 微粒子"""
    import tkinter as tk
    from PIL import Image, ImageTk
    import math
    import random

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes('-topmost', True)
    root.configure(bg='black')
    root.wm_attributes('-transparentcolor', 'black')

    # 窗口尺寸：正方形构图，Logo 居中偏上，下方留文字空间
    win_w, win_h = 380, 440
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - win_w) // 2
    y = (sh - win_h) // 2
    root.geometry(f'{win_w}x{win_h}+{x}+{y}')

    canvas = tk.Canvas(root, width=win_w, height=win_h, bg='black', highlightthickness=0)
    canvas.pack()

    # ====== 构图参数 ======
    # Logo 区域：取窗口上半部分的正方形区域
    logo_size = 180                              # Logo 渲染尺寸
    logo_center_x = win_w // 2                   # 水平居中
    logo_center_y = win_h // 2 - 35              # 垂直偏上，为下方文字留空间
    # Logo 包围盒：用于粒子环绕
    logo_bottom = logo_center_y + logo_size // 2
    # 文字位置：紧贴 Logo 下方
    text_y_main = logo_bottom + 28               # "电脑医生"
    text_y_sub = text_y_main + 38                # "PC Doctor"

    # 加载原始 Logo
    base_logo = None
    try:
        if getattr(sys, 'frozen', False):
            logo_path = os.path.join(sys._MEIPASS, 'my_logo.ico')
        else:
            logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'my_logo.ico')
        img = Image.open(logo_path)
        # 直接取第一帧（通常就是最大尺寸）
        base_logo = img.resize((logo_size, logo_size), Image.Resampling.LANCZOS).convert('RGBA')
    except Exception:
        pass

    # 粒子系统：围绕 Logo 区域环形分布
    particles = []
    def spawn_particle():
        cx, cy = logo_center_x, logo_center_y
        angle = random.uniform(0, 2 * math.pi)
        # 粒子在 Logo 包围盒外缘生成
        radius_x = logo_size // 2 + random.randint(10, 40)
        radius_y = logo_size // 2 + random.randint(10, 40)
        px = cx + radius_x * math.cos(angle)
        py = cy + radius_y * math.sin(angle)
        vx = random.uniform(-0.3, 0.3)
        vy = random.uniform(-0.9, -0.2)          # 向上飘
        life = random.uniform(1.8, 3.2)
        size = random.randint(2, 4)
        particles.append([px, py, vx, vy, size, 0.0, life])

    last_spawn = time.time()
    max_particles = 35
    start_time = time.time()

    def create_highlighted_logo(highlight_y_ratio, highlight_width_ratio=0.12):
        """根据高亮在 Logo 中的相对位置（0~1），返回带亮度渐变的 PhotoImage"""
        if base_logo is None:
            return None
        logo_copy = base_logo.copy()
        pixels = logo_copy.load()
        w, h = logo_copy.size

        highlight_center = int(h * highlight_y_ratio)
        half_width = max(1, int(h * highlight_width_ratio / 2))

        for py in range(h):
            dist = abs(py - highlight_center)
            if dist < half_width:
                factor = 1.0 - (dist / half_width)
                brightness_boost = 1.0 + factor * 0.8
                for px in range(w):
                    r, g, b, a = pixels[px, py]
                    if a > 0:
                        r = min(255, int(r * brightness_boost))
                        g = min(255, int(g * brightness_boost))
                        b = min(255, int(b * brightness_boost))
                        pixels[px, py] = (r, g, b, a)

        return ImageTk.PhotoImage(logo_copy)

    def update():
        nonlocal last_spawn
        canvas.delete('all')

        elapsed = time.time() - start_time
        t = min(elapsed / duration, 1.0)

        # 整体淡入
        alpha = 1 - (1 - t) ** 3

        # 高亮扫描：来回扫动
        scan_speed = 1.2
        scan_progress = (elapsed / scan_speed) % 1.0
        if int(elapsed / scan_speed) % 2 == 0:
            highlight_pos = scan_progress   # 上 → 下
        else:
            highlight_pos = 1.0 - scan_progress  # 下 → 上

        # Logo + 高亮扫描效果
        if base_logo is not None:
            highlighted = create_highlighted_logo(highlight_pos, highlight_width_ratio=0.12)
            if highlighted is not None:
                canvas.create_image(logo_center_x, logo_center_y, image=highlighted)
                canvas.highlighted_ref = highlighted

        # 微粒子（围绕 Logo 区域）
        now = time.time()
        if len(particles) < max_particles and now - last_spawn > 0.10:
            spawn_particle()
            last_spawn = now

        for p in particles[:]:
            p[0] += p[2]
            p[1] += p[3]
            p[5] += 0.02
            life_ratio = p[5] / p[6] if p[6] > 0 else 1.0
            if life_ratio >= 1.0 or p[1] < 0 or p[1] > win_h:
                particles.remove(p)
                continue
            particle_alpha = min(life_ratio * 2, 2 - life_ratio * 2) * alpha
            # 光晕层
            glow_alpha = int(255 * particle_alpha * 0.35)
            glow_color = f'#{glow_alpha:02x}{glow_alpha:02x}{glow_alpha:02x}'
            canvas.create_oval(p[0] - p[4] - 3, p[1] - p[4] - 3,
                               p[0] + p[4] + 3, p[1] + p[4] + 3,
                               fill=glow_color, outline='', tags='particle')
            # 核心
            core_alpha = min(255, int(255 * particle_alpha * 1.3))
            core_color = f'#{core_alpha:02x}{core_alpha:02x}{core_alpha:02x}'
            canvas.create_oval(p[0] - p[4], p[1] - p[4],
                               p[0] + p[4], p[1] + p[4],
                               fill=core_color, outline='', tags='particle')

        # 文字（紧跟 Logo 下方）
        rv = int(255 * alpha)
        text_color = f'#{rv:02x}{rv:02x}{rv:02x}'
        canvas.create_text(win_w // 2, text_y_main,
                           text="电脑医生", font=("Microsoft YaHei", 20, "bold"), fill=text_color)
        rv2 = int(255 * alpha * 0.7)
        sub_color = f'#{rv2:02x}{rv2:02x}{rv2:02x}'
        canvas.create_text(win_w // 2, text_y_sub,
                           text="PC Doctor", font=("Microsoft YaHei", 12), fill=sub_color)

        if t < 1.0:
            root.after(30, update)

    update()
    root.after(duration, root.destroy)
    root.mainloop()


def force_exit(page=None, sockets=None):
    """强制退出整个 Python 进程（忽略 Eel 传来的参数）"""
    try:
        eel.sleep(0.01)  # 给 Eel 极短时间清理
    except Exception:
        pass
    os._exit(0)






@eel.expose
def open_url(url):
    webbrowser.open(url)


@eel.expose
def exit_app():
    force_exit()

if __name__ == '__main__':
    # 1. 显示启动画面
    show_splash(2500)

    # 2. 启动网速悬浮窗线程（显式从 speed_float 导入，确保 PyInstaller 能检测到）
    from speed_float import start_monitor
    import threading
    t = threading.Thread(target=start_monitor, daemon=True)
    t.start()
    
    # 3. 延迟加载知识库：放入后台线程，主窗口（Eel）可提前出现，提升启动速度
    def _load_knowledge_bg():
        try:
            # 如果知识库为空且存在JSON文件，自动导入（优先 v2 新格式）
            if not learning.load_knowledge_data():
                if os.path.exists(KB_V2_PATH):
                    learning.import_from_json(KB_V2_PATH)
                elif os.path.exists(KB_PATH):
                    learning.import_from_json(KB_PATH)
        except Exception as e:
            print(f"[启动] 知识库后台加载异常: {e}")
        finally:
            kb_ready.set()
    _kb_thread = threading.Thread(target=_load_knowledge_bg, daemon=True)
    _kb_thread.start()
    # 最多等待 5 秒，避免极端情况下主窗口被无限期阻塞
    kb_ready.wait(timeout=5)

    # 4. 启动 Eel 主窗口
    import traceback
    for try_mode, try_name in [('chrome', 'Chrome'), ('edge', 'Edge'), (None, '默认浏览器')]:
        try:
            kwargs = {'size': (900, 700), 'close_callback': force_exit}
            if try_mode is not None:
                kwargs['mode'] = try_mode
            eel.start('index.html', **kwargs)
            break
        except Exception as e:
            print(f"[启动] {try_name} 模式失败: {e}")
            traceback.print_exc()
    else:
        print("\n[致命错误] 所有浏览器模式都失败了，请检查浏览器是否正常安装。")
        input("按回车键退出...")

# ===== 以下为恢复的历史函数

@eel.expose
def clean_leftovers(files_to_delete, reg_keys_to_delete):
    """删除指定的残留文件和注册表项"""
    result = {"files_deleted": 0, "reg_deleted": 0, "errors": []}
    for f in files_to_delete:
        try:
            if os.path.isfile(f):
                os.remove(f)
                result["files_deleted"] += 1
            elif os.path.isdir(f):
                # 小心删除，只删除空目录或直接删除整个残留目录（用户确认过的）
                shutil.rmtree(f, ignore_errors=True)
                result["files_deleted"] += 1
        except Exception as e:
            result["errors"].append(f"删除文件失败 {f}: {str(e)}")
    for reg in reg_keys_to_delete:
        try:
            # 解析注册表路径 HKEY_LOCAL_MACHINE\SOFTWARE\...
            parts = reg.split("\\", 1)
            if len(parts) == 2 and parts[0] == "HKEY_LOCAL_MACHINE":
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, parts[1], 0, winreg.KEY_ALL_ACCESS)
                winreg.DeleteKey(key, "")
                winreg.CloseKey(key)
                result["reg_deleted"] += 1
        except Exception as e:
            result["errors"].append(f"删除注册表失败 {reg}: {str(e)}")
    return result


@eel.expose
def delete_software_reg_entry(reg_key):
    """删除指定软件的注册表卸载条目（用于清理已卸载但残留注册表的软件）"""
    try:
        parts = reg_key.split("\\", 1)
        if len(parts) == 2 and parts[0] == "HKEY_LOCAL_MACHINE":
            # 打开父键并删除子键
            parent_path, subkey_name = parts[1].rsplit("\\", 1)
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, parent_path, 0, winreg.KEY_ALL_ACCESS)
            winreg.DeleteKey(key, subkey_name)
            winreg.CloseKey(key)
            return {"success": True, "message": "注册表条目已删除。"}
        else:
            return {"success": False, "message": "不支持的注册表路径格式。"}
    except FileNotFoundError:
        return {"success": False, "message": "该注册表项已不存在。"}
    except PermissionError:
        return {"success": False, "message": "权限不足，请以管理员身份运行。"}
    except Exception as e:
        return {"success": False, "message": f"删除失败: {str(e)}"}


@eel.expose
def get_installed_software():
    """获取已安装软件列表，返回名称、版本、大小、卸载命令等"""
    import winreg
    software_list = []
    uninstall_keys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for key_path in uninstall_keys:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ)
            for i in range(0, winreg.QueryInfoKey(key)[0]):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)
                    name = ""
                    version = ""
                    publisher = ""
                    uninstall_string = ""
                    install_location = ""
                    size_mb = 0
                    try:
                        name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                    except Exception:
                        continue  # 跳过没有显示名称的项
                    try:
                        version = winreg.QueryValueEx(subkey, "DisplayVersion")[0]
                    except Exception:
                        pass
                    try:
                        publisher = winreg.QueryValueEx(subkey, "Publisher")[0]
                    except Exception:
                        pass
                    try:
                        uninstall_string = winreg.QueryValueEx(subkey, "UninstallString")[0]
                    except Exception:
                        pass
                    try:
                        install_location = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                    except Exception:
                        pass
                    try:
                        # 尝试获取大小（有些软件有 EstimatedSize，单位 KB）
                        size = winreg.QueryValueEx(subkey, "EstimatedSize")[0]
                        size_mb = round(int(size) / 1024, 1) if size else 0
                    except Exception:
                        pass
                    # 判断是否为有效的可卸载软件（有卸载命令）
                    is_valid = bool(uninstall_string)
                    reg_full_path = f"HKEY_LOCAL_MACHINE\\{key_path}\\{subkey_name}"
                    
                    software_list.append({
                        "name": name,
                        "version": version,
                        "publisher": publisher,
                        "uninstall_string": uninstall_string,
                        "install_location": install_location,
                        "size_mb": size_mb,
                        "is_valid": is_valid,
                        "reg_key": reg_full_path
                    })
                    winreg.CloseKey(subkey)
                except Exception:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass
    # 按名称排序
    software_list.sort(key=lambda x: x["name"].lower())
    return software_list


@eel.expose
def launch_defender_scan(scan_type: str = "quick"):
    """启动 Windows Defender 扫描（quick/full/custom）并以低权限窗口通知用户"""
    try:
        if scan_type == "full":
            subprocess.Popen(
                ['powershell', '-Command', 'Start-MpScan -ScanType FullScan'],
                creationflags=CREATE_NO_WINDOW
            )
            return {"success": True, "message": "已启动完整扫描，请稍后查看结果。"}
        else:
            os.startfile("windowsdefender://threat")
            return {"success": True, "message": "已打开 Windows 安全中心。"}
    except Exception as e:
        return {"success": False, "message": f"启动失败：{str(e)[:100]}"}


@eel.expose
def move_file(source, target_dir):
    """移动文件到指定目录"""
    try:
        if not os.path.exists(target_dir):
            os.makedirs(target_dir, exist_ok=True)
        filename = os.path.basename(source)
        dest = os.path.join(target_dir, filename)
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(target_dir, f"{base}_{counter}{ext}")
            counter += 1
        shutil.move(source, dest)
        return {"success": True, "message": f"已移动到 {dest}"}
    except Exception as e:
        return {"success": False, "message": str(e)}



@eel.expose
def open_windows_security():
    """打开 Windows 安全中心"""
    try:
        os.startfile("windowsdefender://")
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": str(e)[:100]}



@eel.expose
def scan_leftovers(software_name, install_location):
    """扫描残留文件和注册表项"""
    leftovers = {"files": [], "reg_keys": []}
    # 1. 常见残留目录
    search_paths = []
    if install_location and os.path.exists(install_location):
        search_paths.append(install_location)
    # 尝试从软件名推测 AppData 中的目录
    appdata_local = os.getenv('LOCALAPPDATA')
    appdata_roaming = os.getenv('APPDATA')
    programdata = os.getenv('PROGRAMDATA')
    # 简单搜索包含软件名的文件夹（浅层搜索，避免耗时）
    for base in [appdata_local, appdata_roaming, programdata]:
        if base and os.path.exists(base):
            try:
                for item in os.listdir(base):
                    if software_name.lower() in item.lower():
                        full_path = os.path.join(base, item)
                        search_paths.append(full_path)
            except Exception:
                pass
    # 收集残留文件列表（只展示顶层和二级文件，避免太多）
    for sp in set(search_paths):
        if os.path.exists(sp):
            for root, dirs, files in os.walk(sp):
                for f in files:
                    leftovers["files"].append(os.path.join(root, f))
                # 只走两层
                if root.count(os.sep) - sp.count(os.sep) > 1:
                    dirs.clear()
                break  # 只显示目录本身和直接子文件，如需深度扫描可去掉break
    # 2. 注册表残留扫描
    reg_paths_to_check = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE",
        r"SOFTWARE\WOW6432Node",
    ]
    for reg_path in reg_paths_to_check:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_READ)
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    if software_name.lower() in subkey_name.lower():
                        leftovers["reg_keys"].append(f"HKEY_LOCAL_MACHINE\\{reg_path}\\{subkey_name}")
                except Exception:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass
    return leftovers


@eel.expose
def uninstall_software(uninstall_string, software_name):
    """执行卸载命令（静默或等待完成），返回卸载是否成功"""
    try:
        # 有些卸载命令需要加参数实现静默卸载，这里不做强制，直接执行
        # 在 Windows 中，通常卸载命令会弹出界面，需要用户交互，我们只能等待
        process = subprocess.Popen(uninstall_string, shell=True, creationflags=CREATE_NO_WINDOW)
        process.wait()
        return {"success": True, "message": f"{software_name} 卸载完成。"}
    except Exception as e:
        return {"success": False, "message": f"卸载失败: {str(e)}"}

