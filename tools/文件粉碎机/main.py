# -*- coding: utf-8 -*-
"""
文件粉碎机 - 独立子工具
功能：多次覆写后彻底删除文件，无法恢复，适合处理隐私文件。
完全独立运行，不依赖电脑医生主程序。
打包：pyinstaller -F -w main.py
"""
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

PASSES = 3  # 覆写次数


def shred_file(path):
    """多次覆写后删除，返回是否成功"""
    if not os.path.isfile(path):
        return False
    try:
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            for _ in range(PASSES):
                f.seek(0)
                # 用随机数据覆写
                remaining = size
                while remaining > 0:
                    chunk = os.urandom(min(1024 * 1024, remaining))
                    f.write(chunk)
                    remaining -= len(chunk)
                f.flush()
                os.fsync(f.fileno())
        os.remove(path)
        return True
    except Exception:
        try:
            os.remove(path)
        except Exception:
            pass
        return False


class FileShredderApp:
    def __init__(self, root):
        self.root = root
        root.title("文件粉碎机 - 独立工具")
        root.geometry("640x520")
        root.resizable(False, False)

        ttk.Label(root, text="文件粉碎机", font=("Microsoft YaHei", 18, "bold")).pack(pady=(14, 2))
        ttk.Label(root, text=f"多次覆写({PASSES}次)后彻底删除，无法恢复，请谨慎操作",
                  font=("Microsoft YaHei", 10), foreground="#cf222e").pack(pady=(0, 10))

        btn_frame = ttk.Frame(root)
        btn_frame.pack(pady=4)
        ttk.Button(btn_frame, text="添加文件", command=self.add_files).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="添加文件夹", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="移除选中", command=self.remove_sel).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="清空", command=self.clear).pack(side="left", padx=6)

        self.listbox = tk.Listbox(root, font=("Microsoft YaHei", 10), height=16, activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=16, pady=8)

        ttk.Button(root, text="🔥 开始粉碎选中文件", command=self.shred).pack(pady=8)

        self.files = []

    def add_files(self):
        paths = filedialog.askopenfilenames(title="选择要粉碎的文件")
        for p in paths:
            if p not in self.files:
                self.files.append(p)
        self.render()

    def add_folder(self):
        d = filedialog.askdirectory(title="选择要粉碎的文件夹")
        if d:
            for root_dir, _, files in os.walk(d):
                for f in files:
                    p = os.path.join(root_dir, f)
                    if p not in self.files:
                        self.files.append(p)
        self.render()

    def remove_sel(self):
        for i in reversed(self.listbox.curselection()):
            del self.files[i]
        self.render()

    def clear(self):
        self.files = []
        self.render()

    def render(self):
        self.listbox.delete(0, tk.END)
        for p in self.files:
            self.listbox.insert(tk.END, p)

    def shred(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先选择要粉碎的文件。")
            return
        if not messagebox.askyesno("危险操作", "文件将被彻底粉碎且无法恢复，确定继续吗？"):
            return
        ok = 0
        fail = 0
        for i in sel:
            p = self.files[i]
            if shred_file(p):
                ok += 1
            else:
                fail += 1
        messagebox.showinfo("完成", f"粉碎完成：成功 {ok} 个，失败 {fail} 个")
        # 移除已粉碎文件
        for i in reversed(sel):
            if i < len(self.files):
                del self.files[i]
        self.render()


def main():
    root = tk.Tk()
    FileShredderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
