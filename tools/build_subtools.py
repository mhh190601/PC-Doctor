# -*- coding: utf-8 -*-
"""打包三个独立子工具为单文件 EXE。"""
import os
import subprocess
import sys

TOOLS = {
    "隐私清理": "隐私清理/main.py",
    "启动项管理": "启动项管理/main.py",
    "文件粉碎机": "文件粉碎机/main.py",
}

DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
os.makedirs(DIST, exist_ok=True)


def build():
    for name, src in TOOLS.items():
        out_name = name + ".exe"
        print(f"==> 打包 {name} ...")
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "-F", "-w",
            "--name", name,
            "--distpath", DIST,
            "--workpath", os.path.join(DIST, "build"),
            "--specpath", os.path.join(DIST, "spec"),
            src,
        ]
        try:
            subprocess.run(cmd, check=True)
            print(f"    OK -> {os.path.join(DIST, out_name)}")
        except subprocess.CalledProcessError as e:
            print(f"    打包失败: {e}")


if __name__ == "__main__":
    build()
