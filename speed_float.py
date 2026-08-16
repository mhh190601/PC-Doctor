import tkinter as tk
import psutil
import time

class SpeedMonitor:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("网速监控")
        self.root.geometry("160x40+1000+50")  # 宽160，高40，默认出现在右上角
        self.root.overrideredirect(True)      # 去掉标题栏
        self.root.attributes('-topmost', True) # 置顶
        self.root.attributes('-alpha', 0.7)    # 半透明
        
        # 颜色配置
        self.bg_color = "#2c3e50"
        self.fg_color = "#ecf0f1"
        self.root.configure(bg=self.bg_color)
        
        # 显示标签
        self.label = tk.Label(
            self.root,
            text="⬆ 0 KB/s\n⬇ 0 KB/s",
            font=("Microsoft YaHei", 10),
            bg=self.bg_color,
            fg=self.fg_color,
            justify='left'
        )
        self.label.pack(expand=True, fill='both')
        
        # 支持拖拽移动
        self.label.bind("<Button-1>", self.start_move)
        self.label.bind("<B1-Motion>", self.on_move)
        # 右键退出
        self.label.bind("<Button-3>", lambda e: self.root.destroy())
        
        # 记录初始网速数据
        self.last_net = psutil.net_io_counters()
        self.last_time = time.time()
        
        # 绑定关闭协议，防止 Tkinter 窗口关闭时报错
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # 启动更新循环
        self.update_speed()
        
    def on_closing(self):
        self.root.destroy()
        
    def start_move(self, event):
        self.x = event.x
        self.y = event.y
        
    def on_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")
        
    def update_speed(self):
        """每秒更新网速"""
        current_net = psutil.net_io_counters()
        current_time = time.time()
        elapsed = current_time - self.last_time
        
        if elapsed >= 1.0:
            upload = (current_net.bytes_sent - self.last_net.bytes_sent) / elapsed
            download = (current_net.bytes_recv - self.last_net.bytes_recv) / elapsed
            
            # 格式化速度
            def format_speed(speed):
                if speed < 1024:
                    return f"{speed:.0f} B/s"
                elif speed < 1024*1024:
                    return f"{speed/1024:.1f} KB/s"
                else:
                    return f"{speed/(1024*1024):.1f} MB/s"
            
            self.label.config(text=f"⬆ {format_speed(upload)}\n⬇ {format_speed(download)}")
            self.last_net = current_net
            self.last_time = current_time
        
        self.root.after(1000, self.update_speed)
    
    def run(self):
        self.root.mainloop()

def start_monitor():
    monitor = SpeedMonitor()
    monitor.run()
