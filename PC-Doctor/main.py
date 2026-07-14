"""
电脑医生桌面版 - 带自学习AI的本地优化工具
"""

import sys
import os
import eel
import subprocess
import shutil
import tempfile
import socket
import threading
import psutil
import learning  # 导入自学习模块

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

# ================== 原有优化功能 (保持不变) ==================
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
        subprocess.run(['cleanmgr', '/sagerun:1'], capture_output=True, timeout=120)
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
                winreg.CloseKey(key)
            except Exception:
                pass
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
        return "发现以下可疑软件目录：\n" + "\n".join(found) + "\n建议在“应用和功能”中卸载它们。"
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

import ctypes
from ctypes import wintypes

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
                    except:
                        pass
                    finally:
                        CloseHandle(handle)
            except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                continue
                
        return f"内存优化完成，已清理 {released_count} 个进程的工作内存。\n\n💡 提示：\n• 释放的内存是暂时不用的物理内存，不会影响正在运行的程序。\n• 如果以管理员身份运行本软件，清理效果会更好。"
    except Exception as e:
        return f"内存优化失败：{str(e)}\n请尝试以管理员身份运行本软件。"

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
    report_parts.append("\n🎉 所有优化项目已完成！")
    return "\n".join(report_parts)

# ================== 自学习AI诊断接口 ==================
@eel.expose
def ai_diagnose(problem_description):
    """智能诊断：先判断是否电脑问题，再查本地知识库"""

    # 第一步：判断是不是电脑相关问题
    pc_keywords = [
        '电脑', '计算机', '笔记本', '台式', '系统', 'windows', 'win', 'mac', 'macOS',
        '卡', '慢', '卡顿', '死机', '蓝屏', '黑屏', '花屏', '重启', '关机', '开机',
        '内存', '硬盘', 'CPU', '显卡', '主板', '电源', '散热', '风扇', '驱动',
        '网络', '上网', 'WiFi', 'wifi', '宽带', '路由', 'DNS', 'IP',
        '病毒', '杀毒', '防火墙', '安全', '弹窗', '广告', '流氓', '软件',
        'C盘', 'D盘', '磁盘', '空间', '清理', '优化', '卡死', '闪退', '崩溃', '报错',
        '安装', '卸载', '更新', '升级', '浏览器', '输入法', '办公', '游戏',
        '声音', '没声音', '画面', '鼠标', '键盘', '屏幕', '分辨率'
    ]
    
    is_pc_related = any(keyword in problem_description for keyword in pc_keywords)

    if not is_pc_related:
        # 非电脑问题，直接给出友好提示
        return {
            "success": True,
            "answer": "👋 你好！我是电脑医生，只擅长回答电脑相关问题哦。\n\n请描述你的电脑遇到了什么问题，比如：\n• 电脑卡顿怎么办\n• C盘满了如何清理\n• 电脑蓝屏了怎么解决",
            "knowledge_id": None,
            "score": 0,
            "source": "local"
        }

    # 第二步：是电脑问题，查本地知识库
    answer, knowledge_id, score = learning.match_best_answer(problem_description)

    if answer:
        return {
            "success": True,
            "answer": answer,
            "knowledge_id": knowledge_id,
            "score": score,
            "source": "local"
        }
    else:
        # 知识库没匹配到
        return {
            "success": False,
            "message": "抱歉，我暂时没有遇到这个问题。你可以尝试左侧的优化工具，或者换个更具体的问法。",
            "knowledge_id": None
        }

@eel.expose
def submit_feedback(knowledge_id, is_helpful, user_question):
    """提交用户反馈"""
    learning.record_feedback(knowledge_id, is_helpful, user_question)
    return "反馈已记录，感谢你的帮助！"

@eel.expose
def add_user_knowledge(question, answer):
    """用户补充新知识"""
    learning.add_new_knowledge(question, answer)
    return "新知识已录入，谢谢你的贡献！"

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
    except:
        info['uptime'] = "无法获取"
    
    # 2. 上次开机耗时（通过事件日志ID 100计算）
    try:
        # 使用PowerShell查询最近两次启动事件
        cmd = 'powershell -Command "Get-WinEvent -FilterHashtable @{LogName=\'System\'; ID=100} -MaxEvents 2 | Select-Object -ExpandProperty TimeCreated"'
        output = subprocess.check_output(cmd, shell=True, text=True)
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
            else:
                mins, secs = divmod(total_seconds, 60)
                info['last_boot_time'] = f"{int(mins)}分钟{int(secs)}秒"
        else:
            info['last_boot_time'] = "数据不足，请重启后再试"
    except:
        info['last_boot_time'] = "无法获取（可能需要管理员权限）"
    
    return info

# ================== 文件夹空间树形分析 ==================
import stat

@eel.expose
def scan_directory_tree(root_path="C:\\", max_depth=3, top_n=20):
    """
    扫描目录树，返回文件夹大小聚合结构
    max_depth: 扫描深度，避免太深导致性能问题
    top_n: 每个文件夹只返回大小排名前N的子项
    """
    def get_size(path):
        """获取文件或文件夹的总大小（递归）"""
        total = 0
        try:
            if os.path.isfile(path):
                return os.path.getsize(path)
            elif os.path.isdir(path):
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
        return total

    def build_tree(current_path, current_depth=0):
        """构建树结构，只返回top_n子项"""
        try:
            items = []
            with os.scandir(current_path) as it:
                for entry in it:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        size = get_size(entry.path) if not is_dir else 0
                        items.append({
                            'name': entry.name,
                            'path': entry.path,
                            'is_dir': is_dir,
                            'size_mb': round(size / (1024 * 1024), 2),
                            'children': []
                        })
                    except (OSError, PermissionError):
                        continue
            
            # 对于文件夹，计算总大小 = 内部所有文件大小之和
            for item in items:
                if item['is_dir']:
                    total = get_size(item['path'])
                    item['size_mb'] = round(total / (1024 * 1024), 2)
            
            # 按大小降序排序，只保留top_n个最大的
            items.sort(key=lambda x: x['size_mb'], reverse=True)
            items = items[:top_n]
            
            # 如果未达到最大深度，继续展开文件夹
            if current_depth < max_depth:
                for item in items:
                    if item['is_dir']:
                        item['children'] = build_tree(item['path'], current_depth + 1)
            
            return items
        except (OSError, PermissionError):
            return []

    # 确保路径格式正确
    if not os.path.exists(root_path):
        return {"error": f"路径 {root_path} 不存在"}
    
    root_name = os.path.basename(root_path.rstrip('\\/')) or root_path
    total_size = get_size(root_path)
    
    tree = {
        'name': root_name,
        'path': root_path,
        'size_mb': round(total_size / (1024 * 1024), 2),
        'is_dir': True,
        'children': build_tree(root_path, 0)
    }
    return tree

# ================== 启动 ==================
if __name__ == '__main__':
    # 启动网速悬浮窗线程（显式从 speed_float 导入，确保 PyInstaller 能检测到）
    from speed_float import start_monitor
    import threading
    t = threading.Thread(target=start_monitor, daemon=True)
    t.start()
    
    # 如果知识库为空且存在JSON文件，自动导入
    if not learning.load_knowledge_data() and os.path.exists('knowledge_base.json'):
        learning.import_from_json('knowledge_base.json')
    
    eel.start('index.html', size=(900, 700))