# -*- coding: utf-8 -*-
"""
隐私清理 - 独立子工具
功能：清理浏览器缓存/历史、Windows 临时文件、微信/QQ 缓存等，保护隐私。
完全独立运行，不依赖电脑医生主程序。
打包：pyinstaller -F -w main.py
"""
import os
import shutil
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext


HOME = os.path.expanduser("~")


def _iter_clean_targets():
    """返回 (显示名, 路径) 列表，路径不存在则跳过"""
    targets = []

    # 浏览器缓存/历史
    chrome_base = os.path.join(HOME, "AppData", "Local", "Google", "Chrome", "User Data")
    if os.path.isdir(chrome_base):
        for prof in ["Default", "Profile 1", "Profile 2"]:
            d = os.path.join(chrome_base, prof)
            if os.path.isdir(d):
                targets.append((f"Chrome 缓存 ({prof})", os.path.join(d, "Cache")))
                targets.append((f"Chrome 历史 ({prof})", os.path.join(d, "History")))
                targets.append((f"Chrome Cookies ({prof})", os.path.join(d, "Cookies")))

    edge_base = os.path.join(HOME, "AppData", "Local", "Microsoft", "Edge", "User Data")
    if os.path.isdir(edge_base):
        for prof in ["Default", "Profile 1"]:
            d = os.path.join(edge_base, prof)
            if os.path.isdir(d):
                targets.append((f"Edge 缓存 ({prof})", os.path.join(d, "Cache")))
                targets.append((f"Edge 历史 ({prof})", os.path.join(d, "History")))

    firefox_base = os.path.join(HOME, "AppData", "Roaming", "Mozilla", "Firefox", "Profiles")
    if os.path.isdir(firefox_base):
        for name in os.listdir(firefox_base):
            d = os.path.join(firefox_base, name)
            if os.path.isdir(d):
                targets.append((f"Firefox 缓存 ({name})", os.path.join(d, "cache2")))
                targets.append((f"Firefox 历史 ({name})", os.path.join(d, "places.sqlite")))

    # Windows 临时文件
    targets.append(("系统临时文件", os.path.join(os.environ.get("TEMP", ""))))
    targets.append(("Windows 临时目录", r"C:\Windows\Temp"))
    targets.append(("最近使用记录", os.path.join(HOME, "AppData", "Roaming", "Microsoft", "Windows", "Recent")))
    targets.append(("回收站(仅清空当前用户)", os.path.join(HOME, "$Recycle.Bin")))

    # 微信/QQ 缓存
    wx = os.path.join(HOME, "AppData", "Local", "Temp", "WXWork")
    if os.path.isdir(wx):
        targets.append(("企业微信缓存", wx))
    wx2 = os.path.join(HOME, "Documents", "WeChat Files")
    if os.path.isdir(wx2):
        targets.append(("微信文件缓存", wx2))

    return targets


def _human(size):
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _dir_size(path):
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
    except Exception:
        pass
    return total


def _remove(path):
    """删除文件或目录，返回释放字节数"""
    freed = 0
    try:
        if os.path.isfile(path):
            freed = os.path.getsize(path)
            os.remove(path)
        elif os.path.isdir(path):
            freed = _dir_size(path)
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
    return freed


class PrivacyCleanerApp:
    def __init__(self, root):
        self.root = root
        root.title("隐私清理 - 独立工具")
        root.geometry("620x560")
        root.resizable(False, False)
        try:
            root.iconbitmap()
        except Exception:
            pass

        style = ttk.Style()
        style.theme_use("clam")

        ttk.Label(root, text="隐私清理", font=("Microsoft YaHei", 18, "bold")).pack(pady=(14, 2))
        ttk.Label(root, text="一键清理浏览器缓存、历史记录与系统临时文件，保护隐私",
                  font=("Microsoft YaHei", 10), foreground="#666").pack(pady=(0, 10))

        # 列表框
        frame = ttk.Frame(root)
        frame.pack(fill="both", expand=True, padx=16)
        self.listbox = tk.Listbox(frame, selectmode="multiple", font=("Microsoft YaHei", 10),
                                  height=14, activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, command=self.listbox.yview)
        scroll.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scroll.set)

        self.targets = _iter_clean_targets()
        self.est_var = tk.StringVar(value="预计可释放：0 B")
        ttk.Label(root, textvariable=self.est_var, font=("Microsoft YaHei", 10),
                  foreground="#cf222e").pack(pady=6)

        btn_frame = ttk.Frame(root)
        btn_frame.pack(pady=8)
        ttk.Button(btn_frame, text="全选", command=self.select_all).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="全不选", command=self.select_none).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="开始清理", command=self.clean).pack(side="left", padx=6)

        self.log = scrolledtext.ScrolledText(root, height=6, font=("Consolas", 9))
        self.log.pack(fill="both", padx=16, pady=(0, 12))

        self.refresh()

    def refresh(self):
        self.listbox.delete(0, tk.END)
        self.targets = _iter_clean_targets()
        for name, path in self.targets:
            size = _dir_size(path) if os.path.isdir(path) else (os.path.getsize(path) if os.path.isfile(path) else 0)
            self.listbox.insert(tk.END, f"{name}  ({_human(size)})")
            self.listbox.selection_set(tk.END)
        self.calc_estimate()

    def calc_estimate(self):
        total = 0
        for i in self.listbox.curselection():
            _, path = self.targets[i]
            if os.path.isdir(path):
                total += _dir_size(path)
            elif os.path.isfile(path):
                total += os.path.getsize(path)
        self.est_var.set(f"预计可释放：{_human(total)}")

    def select_all(self):
        self.listbox.selection_set(0, tk.END)
        self.calc_estimate()

    def select_none(self):
        self.listbox.selection_clear(0, tk.END)
        self.calc_estimate()

    def clean(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先选择要清理的项目。")
            return
        if not messagebox.askyesno("确认", "确定要清理选中的隐私数据吗？部分操作不可恢复。"):
            return
        total_freed = 0
        for i in sel:
            name, path = self.targets[i]
            if not (os.path.exists(path) or os.path.islink(path)):
                self.log.insert(tk.END, f"[跳过] {name} 不存在\n")
                continue
            freed = _remove(path)
            total_freed += freed
            self.log.insert(tk.END, f"[已清理] {name}  释放 {_human(freed)}\n")
            self.log.see(tk.END)
        self.log.insert(tk.END, f"==== 共释放 {_human(total_freed)} ====\n")
        self.log.see(tk.END)
        messagebox.showinfo("完成", f"清理完成，共释放 {_human(total_freed)}")
        self.refresh()


def main():
    root = tk.Tk()
    app = PrivacyCleanerApp(root)
    # 列表选择变化时更新预估
    app.listbox.bind("<<ListboxSelect>>", lambda e: app.calc_estimate())
    root.mainloop()


if __name__ == "__main__":
    main()
