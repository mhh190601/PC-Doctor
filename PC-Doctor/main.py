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
import hashlib
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

import string
from ctypes import windll

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
    except:
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
    except:
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
    except:
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
    except:
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
    except:
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
    except:
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
    except:
        checks.append({"name": "内存使用率检查", "status": "warning", "desc": "检测失败"})
    
    # ---------- 7. 开机耗时 ----------
    try:
        boot_info = get_boot_info()
        last_boot_time = boot_info.get('last_boot_time', '')
        if '分钟' in last_boot_time and int(last_boot_time.split('分钟')[0]) > 60:
            score -= 10
            status = 'warning'
            desc = f'上次开机耗时 {last_boot_time}，较慢。'
            issues.append({"name": "开机速度较慢", "level": "warning", "desc": desc})
        else:
            status = 'ok'
            desc = f'上次开机耗时 {last_boot_time}。'
        checks.append({
            "name": "开机耗时检查",
            "status": status,
            "desc": desc
        })
    except:
        checks.append({"name": "开机耗时检查", "status": "warning", "desc": "检测失败"})
    
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
                    except:
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
                        except:
                            pass
                    size_after = get_dir_size(cache_path)
                    freed = size_before - size_after
                    total_files += 1
                    total_size += freed
                    details.append(f"清理 {name} 缓存，释放约 {freed / (1024*1024):.1f} MB")
                except:
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
                except:
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
        except:
            details.append("清理运行历史记录失败")

    # 清空回收站
    if 'recycle_bin' in selected_ids:
        try:
            os.system('cmd /c "echo y| rd /s %systemdrive%\\$Recycle.Bin"')
            details.append("回收站已清空")
        except:
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
                        except:
                            pass
                details.append(f"清理临时文件夹 {tmp}")

    return {
        "total_files": total_files,
        "total_size_mb": round(total_size / (1024*1024), 2),
        "details": details
    }

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
                except:
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
import subprocess

@eel.expose
def get_system_info():
    import subprocess, re
    info = {}
    info['os'] = f"{platform.system()} {platform.release()} ({platform.version()})"
    info['hostname'] = platform.node()

    # CPU 友好名称
    try:
        raw_cpu = platform.processor()
        # 尝试通过 wmic 获取更友好的名称
        result = subprocess.run(
            ['wmic', 'cpu', 'get', 'Name'],
            capture_output=True, text=True, timeout=5
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(lines) > 1:
            info['cpu'] = lines[1]  # 通常第二行是CPU名称
        else:
            info['cpu'] = raw_cpu if raw_cpu else "无法识别"
    except:
        info['cpu'] = platform.processor() or "无法识别"

    # 内存
    try:
        mem = psutil.virtual_memory()
        info['memory_total'] = round(mem.total / (1024**3), 1)
    except:
        info['memory_total'] = 0

    # 显卡：优先取独显，排除虚拟设备
    try:
        result = subprocess.run(
            ['wmic', 'path', 'win32_videocontroller', 'get', 'name'],
            capture_output=True, text=True, timeout=5
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
    except:
        info['gpu'] = "获取失败"

    # 主板型号
    try:
        result = subprocess.run(
            ['wmic', 'baseboard', 'get', 'product'],
            capture_output=True, text=True, timeout=5
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        info['motherboard'] = lines[1] if len(lines) > 1 else "未知"
    except:
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
        except:
            pass
    info['disks'] = disks
    return info

# ================== 启动项排行分析 ==================
import winreg

@eel.expose
def get_startup_ranking():
    """获取启动项及其影响评级，按文件大小降序排列"""
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
        except:
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
import winreg
import subprocess
import glob

@eel.expose
def get_installed_software():
    """获取已安装软件列表，返回名称、版本、大小、卸载命令等"""
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
                    except:
                        continue  # 跳过没有显示名称的项
                    try:
                        version = winreg.QueryValueEx(subkey, "DisplayVersion")[0]
                    except:
                        pass
                    try:
                        publisher = winreg.QueryValueEx(subkey, "Publisher")[0]
                    except:
                        pass
                    try:
                        uninstall_string = winreg.QueryValueEx(subkey, "UninstallString")[0]
                    except:
                        pass
                    try:
                        install_location = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                    except:
                        pass
                    try:
                        # 尝试获取大小（有些软件有 EstimatedSize，单位 KB）
                        size = winreg.QueryValueEx(subkey, "EstimatedSize")[0]
                        size_mb = round(int(size) / 1024, 1) if size else 0
                    except:
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
                except:
                    pass
            winreg.CloseKey(key)
        except:
            pass
    # 按名称排序
    software_list.sort(key=lambda x: x["name"].lower())
    return software_list

@eel.expose
def uninstall_software(uninstall_string, software_name):
    """执行卸载命令（静默或等待完成），返回卸载是否成功"""
    try:
        # 有些卸载命令需要加参数实现静默卸载，这里不做强制，直接执行
        # 在 Windows 中，通常卸载命令会弹出界面，需要用户交互，我们只能等待
        process = subprocess.Popen(uninstall_string, shell=True)
        process.wait()
        return {"success": True, "message": f"{software_name} 卸载完成。"}
    except Exception as e:
        return {"success": False, "message": f"卸载失败: {str(e)}"}

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
            except:
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
                except:
                    pass
            winreg.CloseKey(key)
        except:
            pass
    return leftovers

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
        except:
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

# ================== 启动 ==================
import atexit
import signal

def force_exit(page=None, sockets=None):
    """强制退出整个 Python 进程（忽略 Eel 传来的参数）"""
    os._exit(0)

@eel.expose
def exit_app():
    force_exit()

if __name__ == '__main__':
    # 启动网速悬浮窗线程（显式从 speed_float 导入，确保 PyInstaller 能检测到）
    from speed_float import start_monitor
    import threading
    t = threading.Thread(target=start_monitor, daemon=True)
    t.start()
    
    # 如果知识库为空且存在JSON文件，自动导入
    if not learning.load_knowledge_data() and os.path.exists('knowledge_base.json'):
        learning.import_from_json('knowledge_base.json')
    
    import traceback
    # 方案1: 尝试 Chrome 浏览器
    # 方案2: Edge 浏览器  
    # 方案3: 系统默认浏览器（最可靠但会有地址栏）
    for try_mode, try_name in [('chrome', 'Chrome'), ('edge', 'Edge'), (None, '默认浏览器')]:
        try:
            kwargs = {'size': (900, 700), 'close_callback': force_exit}
            if try_mode is not None:
                kwargs['mode'] = try_mode
            eel.start('index.html', **kwargs)
            break  # 如果 start 正常返回（用户关闭窗口），退出循环
        except Exception as e:
            print(f"[启动] {try_name} 模式失败: {e}")
            traceback.print_exc()
    else:
        print("\n[致命错误] 所有浏览器模式都失败了，请检查浏览器是否正常安装。")
        input("按回车键退出...")