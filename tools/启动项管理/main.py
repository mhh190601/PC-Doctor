# -*- coding: utf-8 -*-
"""
启动项管理 - 独立子工具
功能：查看/禁用/启用开机启动程序（注册表 + 启动文件夹），标注影响程度。
完全独立运行，不依赖电脑医生主程序。
打包：pyinstaller -F -w main.py
"""
import os
import tkinter as tk
from tkinter import ttk, messagebox

import winreg

HKCU_RUN = (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
HKLM_RUN = (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run")


def _read_key(hkey, subkey):
    items = []
    try:
        k = winreg.OpenKey(hkey, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except Exception:
        return items
    try:
        idx = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(k, idx)
            except OSError:
                break
            items.append({"name": name, "cmd": value, "hkey": hkey, "subkey": subkey})
            idx += 1
    finally:
        winreg.CloseKey(k)
    return items


def _startup_folder_items():
    items = []
    paths = [
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup"),
        os.path.join(os.environ.get("ALLUSERSPROFILE", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup"),
    ]
    for p in paths:
        if os.path.isdir(p):
            for f in os.listdir(p):
                full = os.path.join(p, f)
                items.append({"name": f, "cmd": f'"{full}"', "folder": full})
    return items


def get_impact(cmd):
    c = cmd.lower()
    if any(k in c for k in ["update", "helper", "cloud", "spotify", "tencent", "qq", "wechat", "baidu", "360", "2345"]):
        return "中"
    return "低"


class StartupManagerApp:
    def __init__(self, root):
        self.root = root
        root.title("启动项管理 - 独立工具")
        root.geometry("720x480")
        root.resizable(False, False)

        ttk.Label(root, text="启动项管理", font=("Microsoft YaHei", 18, "bold")).pack(pady=(14, 2))
        ttk.Label(root, text="查看并禁用/启用开机启动程序，加快开机速度",
                  font=("Microsoft YaHei", 10), foreground="#666").pack(pady=(0, 10))

        cols = ("name", "cmd", "impact")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", height=14)
        self.tree.heading("name", text="名称")
        self.tree.heading("cmd", text="启动命令")
        self.tree.heading("impact", text="影响")
        self.tree.column("name", width=160)
        self.tree.column("cmd", width=380)
        self.tree.column("impact", width=80)
        self.tree.pack(fill="both", expand=True, padx=16, pady=6)

        btn_frame = ttk.Frame(root)
        btn_frame.pack(pady=8)
        ttk.Button(btn_frame, text="刷新", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="禁用选中", command=self.disable_sel).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="启用选中", command=self.enable_sel).pack(side="left", padx=6)

        self.items = []
        self.backup = {}  # name -> (hkey, subkey, cmd)
        self.refresh()

    def refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.items = []
        reg = _read_key(*HKCU_RUN) + _read_key(*HKLM_RUN)
        folder = _startup_folder_items()
        for it in reg + folder:
            impact = get_impact(it["cmd"])
            self.items.append(it)
            self.tree.insert("", "end", values=(it["name"], it["cmd"], impact))

    def _selected(self):
        sel = self.tree.selection()
        idxs = [self.tree.index(s) for s in sel]
        return [self.items[i] for i in idxs if 0 <= i < len(self.items)]

    def disable_sel(self):
        for it in self._selected():
            if "hkey" in it:
                try:
                    k = winreg.OpenKey(it["hkey"], it["subkey"], 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
                    self.backup[it["name"]] = (it["hkey"], it["subkey"], it["cmd"])
                    winreg.DeleteValue(k, it["name"])
                    winreg.CloseKey(k)
                    messagebox.showinfo("已禁用", f"已禁用启动项：{it['name']}")
                except Exception as e:
                    messagebox.showerror("失败", f"禁用 {it['name']} 失败：{e}")
            elif "folder" in it:
                try:
                    os.remove(it["folder"])
                    messagebox.showinfo("已禁用", f"已从启动文件夹移除：{it['name']}")
                except Exception as e:
                    messagebox.showerror("失败", f"移除失败：{e}")
        self.refresh()

    def enable_sel(self):
        for it in self._selected():
            if it["name"] in self.backup:
                hkey, subkey, cmd = self.backup.pop(it["name"])
                try:
                    k = winreg.OpenKey(hkey, subkey, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
                    winreg.SetValueEx(k, it["name"], 0, winreg.REG_SZ, cmd)
                    winreg.CloseKey(k)
                    messagebox.showinfo("已启用", f"已恢复启动项：{it['name']}")
                except Exception as e:
                    messagebox.showerror("失败", f"启用失败：{e}")
        self.refresh()


def main():
    root = tk.Tk()
    StartupManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
