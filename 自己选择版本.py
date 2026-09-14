# -*- coding: utf-8 -*-
"""
DJI视频归档工具
打包命令：pyinstaller -F -w -i dji.ico --add-data "dji.ico;." -n "DJI视频归档工具" "自己选择版本.py"
"""
import os
import re
import time
import tkinter as tk
from tkinter import ttk, filedialog
import threading
import queue
import sys
import shutil
import subprocess
import ctypes

def get_resource_path(relative_path):
    """获取资源路径，区分开发 / pyinstaller exe环境"""
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# 正则：捕获 8位日期(group1)，4位序列编号(group2)
PATTERN = re.compile(r"^DJI_(\d{8})\d{6}_(\d{4})_[A-Z]")
# 文件拷贝块大小：4MB
BLOCK_SIZE = 1024 * 1024 * 4

class DjiArchiveGui:
    def __init__(self, root):
        self.root = root
        self.root.title("DJI视频归档工具")
        self.root.geometry("960x600")
        self.root.resizable(False, False)
        ico_path = get_resource_path("dji.ico")
        self.root.iconbitmap(ico_path)

        # ---------- 业务状态变量 ----------
        self.file_list = []
        self.copy_count = 0
        self.skip_count = 0
        self.total_bytes = 0
        self.is_running = False
        self.msg_queue = queue.Queue()

        # UI业务变量
        self.source_path = None
        self.target_path = None
        self.var_archive_mode = tk.StringVar(value="A")

        # ---------- 界面样式初始化 ----------
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TProgressbar",
                        troughcolor="#E0E0E0",
                        background="#4CAF50")
        style.configure("TFrame", background="#FFFFFF")
        style.configure("Group.TFrame", background="#FFFFFF")
        style.configure("TLabel", background="#FFFFFF", foreground="#1D2129", font=("Microsoft YaHei", 9))
        style.configure("Header.TLabel", background="#FFFFFF", foreground="#1D2129", font=("Microsoft YaHei", 11, "bold"))
        style.configure("GroupTitle.TLabel", background="#FFFFFF", foreground="#1D2129", font=("Microsoft YaHei", 10, "bold"))
        style.configure("Small.TLabel", background="#FFFFFF", foreground="#86909C", font=("Microsoft YaHei", 8))
        style.configure("Value.TLabel", background="#FFFFFF", foreground="#1D2129", font=("Microsoft YaHei", 18, "bold"))
        style.configure("Desc.TLabel", background="#FFFFFF", foreground="#4E5969", font=("Microsoft YaHei", 9))

        style.configure("TButton", font=("Microsoft YaHei", 9), padding=3)
        style.configure("Accent.TButton", background="#E63946", foreground="#FFFFFF", borderwidth=0, focuscolor="none")
        style.map("Accent.TButton", background=[("active", "#D62E3A")])
        style.configure("Normal.TButton", background="#FFFFFF", foreground="#1D2129", borderwidth=1, focuscolor="none")
        style.map("Normal.TButton", background=[("active", "#F5F5F5")])
        root.configure(bg="#FFFFFF")

        # ========== 【顶部：归档设置区】无灰框，普通Frame，三列横向 ==========
        top_setting_frame = ttk.Frame(root, style="TFrame")
        top_setting_frame.place(x=20, y=10, width=920, height=155)

        ttk.Label(top_setting_frame, text="归档设置", style="GroupTitle.TLabel").place(x=0, y=0)
        ttk.Separator(top_setting_frame, orient="horizontal").place(x=0, y=26, width=920)

        # --------第一列：源文件夹【每次选择直接覆盖，清空旧路径】--------
        ttk.Label(top_setting_frame, text="源文件夹", style="GroupTitle.TLabel").place(x=12, y=36)
        self.lb_source = tk.Listbox(top_setting_frame, height=1, font=("Microsoft YaHei",9))
        self.lb_source.place(x=12, y=60, width=280, height=34)
        ttk.Label(top_setting_frame, text="提示：请选择到 X:\\DCIM\\DJI_001 目录", style="Small.TLabel",
                  foreground="#666666").place(x=12, y=136)
        btn_add_src = ttk.Button(top_setting_frame, text="添加源文件夹", style="Accent.TButton", command=self.on_add_source)
        btn_add_src.place(x=12, y=102, width=130, height=32)

        # --------第二列：归档目的地【Listbox + 选择 + 打开按钮】--------
        ttk.Label(top_setting_frame, text="归档目的地", style="GroupTitle.TLabel").place(x=320, y=36)
        self.lb_target = tk.Listbox(top_setting_frame, height=1, font=("Microsoft YaHei",9))
        self.lb_target.place(x=320, y=60, width=280, height=34)

        btn_target = ttk.Button(top_setting_frame, text="选择归档目的地", style="Accent.TButton", command=self.on_select_target)
        btn_target.place(x=320, y=102, width=135, height=32)

        btn_open_target = ttk.Button(top_setting_frame, text="打开目的地文件夹", style="Normal.TButton", command=self.on_open_target)
        btn_open_target.place(x=465, y=102, width=135, height=32)

        # --------第三列：归档目录格式 --------
        ttk.Label(top_setting_frame, text="归档目录格式", style="GroupTitle.TLabel").place(x=630, y=36)
        self.cbo_mode = ttk.Combobox(top_setting_frame, textvariable=self.var_archive_mode, state="readonly")
        self.cbo_mode["values"] = [
            "模式A：年/月/日 (2026\\09\\06)",
            "模式B：年/月日 (2026\\09.06)",
            "模式C：年月日 (20260906)"
        ]
        self.cbo_mode.current(0)
        self.cbo_mode.place(x=630, y=60, width=260, height=28)

        # ========== 分割线 ==========
        sep = ttk.Separator(root, orient="horizontal")
        sep.place(x=20, y=170, width=920)

        # ---------- 主容器整体向上移动 ----------
        main_frame = ttk.Frame(root, style="TFrame")
        main_frame.place(x=20, y=180, width=920, height=420)

        # ========== 左侧面板 ==========
        left_panel = ttk.Frame(main_frame, style="TFrame", width=160)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, expand=False)

        # 【归档统计分组】
        stat_group = ttk.Frame(left_panel, style="Group.TFrame")
        stat_group.pack(fill=tk.X, pady=(0, 16))
        ttk.Label(stat_group, text="归档统计", style="GroupTitle.TLabel").pack(anchor="w")
        ttk.Separator(stat_group, orient="horizontal").pack(fill=tk.X, pady=(10, 12))

        self.var_total_files = tk.StringVar(value="0")
        self.var_copied = tk.StringVar(value="0")
        self.var_skipped = tk.StringVar(value="0")
        self.var_total_size = tk.StringVar(value="0.00 MB")

        # 外层固定宽度容器，禁止内部控件撑大外层，解决布局抖动
        stats_outer = ttk.Frame(stat_group, style="Group.TFrame", width=460)
        stats_outer.pack(fill=tk.X)
        stats_outer.grid_propagate(False)  # 核心：关闭自动尺寸传播，内部不能把外层撑大

        stats_grid = ttk.Frame(stats_outer, style="Group.TFrame")
        stats_grid.pack(fill=tk.BOTH, expand=True)

        self._stat_item(stats_grid, "总文件", self.var_total_files, 0)
        self._stat_item(stats_grid, "已复制", self.var_copied, 1)
        self._stat_item(stats_grid, "已跳过", self.var_skipped, 2)
        self._stat_item(stats_grid, "数据量", self.var_total_size, 3)

        # 【实时速度分组】
        speed_group = ttk.Frame(left_panel, style="Group.TFrame")
        speed_group.pack(fill=tk.X, pady=(0, 16))
        ttk.Label(speed_group, text="实时速度", style="GroupTitle.TLabel").pack(anchor="w")
        ttk.Separator(speed_group, orient="horizontal").pack(fill=tk.X, pady=(10, 12))
        self.var_speed = tk.StringVar(value="0.00 MB/s")
        ttk.Label(speed_group, textvariable=self.var_speed, style="Value.TLabel").pack(anchor="w", pady=(0, 4))
        self.var_percent_single = tk.StringVar(value="单文件 0.00%")
        ttk.Label(speed_group, textvariable=self.var_percent_single, style="Desc.TLabel").pack(anchor="w")

        # 【按钮组：开始归档 + 退出】
        btn_group = ttk.Frame(left_panel, style="Group.TFrame")
        btn_group.pack(fill=tk.X, pady=(0, 16))
        self.btn_start = ttk.Button(btn_group, text="开始归档", style="Accent.TButton", command=self.start_task, width=18)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8), fill=tk.X, expand=True)
        self.btn_exit = ttk.Button(btn_group, text="退出", style="Normal.TButton", command=root.destroy, width=18)
        self.btn_exit.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 【当前状态分组】
        status_group = ttk.Frame(left_panel, style="Group.TFrame")
        status_group.pack(fill=tk.X, pady=(0, 16))
        ttk.Label(status_group, text="当前状态", style="GroupTitle.TLabel").pack(anchor="w")
        ttk.Separator(status_group, orient="horizontal").pack(fill=tk.X, pady=(10, 12))
        self.var_status = tk.StringVar(value="准备就绪")
        ttk.Label(status_group, textvariable=self.var_status, font=("Microsoft YaHei", 10, "bold"), foreground="#E63946").pack(anchor="w")

        # ========== 右侧面板：进度条 + 运行日志 ==========
        right_panel = ttk.Frame(main_frame, style="TFrame")
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(30, 0))
        progress_group = ttk.Frame(right_panel, style="Group.TFrame")
        progress_group.pack(fill=tk.BOTH, expand=True)
        ttk.Label(progress_group, text="处理进度", style="GroupTitle.TLabel").pack(anchor="w")
        ttk.Separator(progress_group, orient="horizontal").pack(fill=tk.X, pady=(10, 12))
        self.var_cur_file = tk.StringVar(value="-")
        ttk.Label(progress_group, textvariable=self.var_cur_file, style="Desc.TLabel").pack(anchor="w", pady=(0, 12))

        upper_frame = ttk.Frame(progress_group, style="Group.TFrame")
        upper_frame.pack(fill=tk.X, expand=False)
        self.progress_total = ttk.Progressbar(upper_frame, style="TProgressbar", orient="horizontal", mode="determinate")
        self.progress_total.pack(fill=tk.X, pady=(0, 8))
        self.var_percent_total = tk.StringVar(value="0.00%")
        ttk.Label(upper_frame, textvariable=self.var_percent_total, style="Desc.TLabel").pack(anchor="w")

        ttk.Label(upper_frame, text="单文件进度", style="Small.TLabel").pack(anchor="w", pady=(12, 4))
        self.progress_single = ttk.Progressbar(upper_frame, style="TProgressbar", orient="horizontal", mode="determinate")
        self.progress_single.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(progress_group, text="运行日志", style="Small.TLabel").pack(anchor="w", pady=(12, 4))
        log_frame = ttk.Frame(progress_group)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        self.summary_text = tk.Text(log_frame, wrap=tk.WORD, font=("Microsoft YaHei", 9),
                                    bg="#FAFAFA", fg="#1D2129", relief="flat", bd=1)
        scrollbar = ttk.Scrollbar(log_frame, command=self.summary_text.yview)
        self.summary_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.summary_text.configure(yscrollcommand=scrollbar.set)

        self.poll_msg()

    # ========= UI回调函数 =========
    def on_add_source(self):
        """每次选择源文件夹：清空旧内容，只保留新路径"""
        p = filedialog.askdirectory(title="选择大疆DJI_001文件夹")
        if not p:
            return
        p_norm = os.path.normpath(p)
        self.lb_source.delete(0, tk.END)
        self.lb_source.insert(tk.END, p_norm)
        self.source_path = p_norm

    def on_select_target(self):
        p = filedialog.askdirectory(title="选择归档根目录")
        if p:
            p_norm = os.path.normpath(p)
            self.lb_target.delete(0, tk.END)
            self.lb_target.insert(tk.END, p_norm)
            self.target_path = p_norm

    def on_open_target(self):
        """打开归档目的地文件夹"""
        if not self.target_path or not os.path.isdir(self.target_path):
            self.msg_queue.put(("log", "提示：请先选择归档目的地"))
            return
        try:
            subprocess.Popen(["explorer", self.target_path])
        except Exception as e:
            self.msg_queue.put(("log", f"打开文件夹失败：{str(e)}"))

    @staticmethod
    def format_bytes(b):
        gb_val = b / (1024 ** 3)
        if gb_val >= 1.0:
            return f"{float(gb_val):.2f} GB"
        else:
            mb_val = b / (1024 ** 2)
            return f"{float(mb_val):.2f} MB"

    def _stat_item(self, parent, label, var, col):
        frame = ttk.Frame(parent, style="Group.TFrame")
        frame.grid(row=0, column=col, sticky="ew", padx=(0, 12))
        ttk.Label(frame, text=label, style="Small.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=var, style="Value.TLabel").pack(anchor="w")
        parent.grid_columnconfigure(col, weight=1)

    def poll_msg(self):
        try:
            while True:
                typ, *args = self.msg_queue.get_nowait()
                if typ == "log":
                    self.summary_text.insert(tk.END, args[0] + "\n")
                    self.summary_text.see(tk.END)
                elif typ == "status":
                    self.var_status.set(args[0])
                elif typ == "curfile":
                    self.var_cur_file.set(args[0])
                elif typ == "stat":
                    tot, cop, skip, b = args
                    self.var_total_files.set(str(tot))
                    self.var_copied.set(str(cop))
                    self.var_skipped.set(str(skip))
                    self.var_total_size.set(self.format_bytes(b))
                elif typ == "totalbar":
                    maxv, val, pct = args
                    self.progress_total["maximum"] = maxv
                    self.progress_total["value"] = val
                    self.var_percent_total.set(f"{pct:.2f}%")
                elif typ == "filebar":
                    maxv, val, sp, pct = args
                    self.progress_single["maximum"] = maxv
                    self.progress_single["value"] = val
                    self.var_percent_single.set(f"单文件 {pct:.1f}%")
                    self.var_speed.set(f"{sp:.2f} MB/s")
                elif typ == "filebar_reset":
                    self.progress_single["value"] = 0
                    self.var_percent_single.set("单文件 0.00%")
                    self.var_speed.set("0.00 MB/s")
                elif typ == "btn":
                    st, st2 = args
                    self.btn_start.config(state=st)
                    self.btn_exit.config(state=st2)
        except queue.Empty:
            pass
        self.root.after(10, self.poll_msg)

    def scan_files(self, src_dir, target_root, mode_text):
        flist = []
        if "模式A" in mode_text:
            mode = "A"
        elif "模式B" in mode_text:
            mode = "B"
        else:
            mode = "C"


        if not os.path.isdir(src_dir):
            return flist
        for fname in os.listdir(src_dir):
            m = PATTERN.match(fname)
            if not m:
                continue
            date_str = m.group(1)
            seq_no = m.group(2)
            yyyy = date_str[0:4]
            mm = date_str[4:6]
            dd = date_str[6:8]

            if mode == "A":
                dest_dir = os.path.join(yyyy, mm, dd)
            elif mode == "B":
                dest_dir = os.path.join(yyyy, f"{mm}.{dd}")
            else:
                dest_dir = os.path.join(f"{yyyy}{mm}{dd}")

            src_f = os.path.join(src_dir, fname)
            dest_f = os.path.join(dest_dir, fname)
            flist.append((src_f, dest_f, dest_dir, fname, seq_no))
        return flist

    def copy_file_stream(self, src_path, dst_path):
        file_size = os.path.getsize(src_path)
        bytes_written = 0
        last_time = time.perf_counter()
        last_bytes = 0
        with open(src_path, "rb") as f_in, open(dst_path, "wb") as f_out:
            while True:
                chunk = f_in.read(BLOCK_SIZE)
                if not chunk:
                    break
                f_out.write(chunk)
                bytes_written += len(chunk)
                now = time.perf_counter()
                delta_t = now - last_time
                delta_b = bytes_written - last_bytes
                speed_mb_s = (delta_b / (1024 * 1024)) / delta_t if delta_t > 0 else 0
                last_time = now
                last_bytes = bytes_written
                pct_single = (bytes_written / file_size) * 100
                self.msg_queue.put(("filebar", file_size, bytes_written, speed_mb_s, pct_single))
        return bytes_written

    def task_thread(self):
        self.is_running = True
        self.msg_queue.put(("btn", "disabled", "disabled"))
        self.copy_count = 0
        self.skip_count = 0
        self.total_bytes = 0

        source_dir = self.source_path
        target_root = self.target_path
        mode_text = self.cbo_mode.get()

        # 校验
        if not source_dir or (not os.path.isdir(source_dir)):
            self.msg_queue.put(("log", "错误：请选择有效的源文件夹"))
            self.is_running = False
            self.msg_queue.put(("btn", "normal", "normal"))
            return
        if not target_root or (not os.path.isdir(target_root)):
            self.msg_queue.put(("log", "错误：请选择有效的归档目的地"))
            self.is_running = False
            self.msg_queue.put(("btn", "normal", "normal"))
            return

        self.msg_queue.put(("log", "开始扫描媒体文件..."))
        self.file_list = self.scan_files(source_dir, target_root, mode_text)
        total = len(self.file_list)
        if total == 0:
            self.msg_queue.put(("status", "未找到媒体文件"))
            self.msg_queue.put(("log", "未找到符合 DJI 命名格式的媒体文件。"))
            self.is_running = False
            self.msg_queue.put(("btn", "normal", "normal"))
            return

        self.msg_queue.put(("stat", total, self.copy_count, self.skip_count, self.total_bytes))
        self.msg_queue.put(("status", "归档进行中"))
        self.msg_queue.put(("log", f"共发现 {total} 个媒体文件。"))
        self.msg_queue.put(("log", "===== 阶段1：开始复制视频文件 =====\n"))

        seq_to_destdir = {}
        # --------------------------
        # 阶段1：拷贝视频
        # --------------------------
        for idx, (src_file, dest_file, dest_dir, filename, seq_no) in enumerate(self.file_list):
            if not self.is_running:
                break
            os.makedirs(dest_dir, exist_ok=True)
            self.msg_queue.put(("curfile", filename[:70]))
            total_pct = (idx + 1) / total * 100
            self.msg_queue.put(("totalbar", total, idx+1, total_pct))

            if seq_no not in seq_to_destdir:
                seq_to_destdir[seq_no] = dest_dir

            if os.path.exists(dest_file):
                self.skip_count += 1
                self.msg_queue.put(("filebar_reset",))
                self.msg_queue.put(("log", f"跳过：{filename}"))
            else:
                wrote = self.copy_file_stream(src_file, dest_file)
                self.copy_count += 1
                self.total_bytes += wrote
                self.msg_queue.put(("log", f"复制：{filename}"))
            self.msg_queue.put(("stat", total, self.copy_count, self.skip_count, self.total_bytes))

        # -------- 自动推导延时摄影源目录 --------
        tl_src_set = set()
        dcim_dir = os.path.dirname(source_dir)
        tl_candidate = os.path.join(dcim_dir, "TIMELAPSE")
        if os.path.isdir(tl_candidate):
            tl_src_set.add(tl_candidate)
        tl_src_list = list(tl_src_set)

        # --------------------------
        # 阶段2：处理延时摄影文件夹
        # --------------------------
        self.msg_queue.put(("log", "\n===== 阶段2：开始处理延时摄影文件夹 ====="))
        if len(seq_to_destdir) == 0:
            self.msg_queue.put(("log", "本次无需要处理的延时摄影编号。"))
        else:
            tl_folder_pattern = re.compile(r"^001_(\d{4})$")
            found_tl = False
            for tl_src_root in tl_src_list:
                if not os.path.isdir(tl_src_root):
                    continue
                for entry in os.scandir(tl_src_root):
                    if not self.is_running:
                        break
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    folder_name = entry.name
                    m = tl_folder_pattern.match(folder_name)
                    if not m:
                        continue
                    seq_tl = m.group(1)
                    if seq_tl in seq_to_destdir:
                        found_tl = True
                        src_tl = entry.path
                        dst_tl = os.path.join(seq_to_destdir[seq_tl], folder_name)
                        if not os.path.exists(dst_tl):
                            try:
                                shutil.copytree(src_tl, dst_tl)
                                self.msg_queue.put(("log", f"复制延时摄影文件夹：{folder_name}"))
                            except Exception as e:
                                self.msg_queue.put(("log", f"【警告】延时摄影 {folder_name} 复制失败：{str(e)}"))
                        else:
                            self.msg_queue.put(("log", f"跳过延时摄影文件夹（已存在）：{folder_name}"))
            if not found_tl:
                self.msg_queue.put(("log", "未找到本次编号对应的延时摄影文件夹"))

        # =========全部任务处理完成=========
        size_text = self.format_bytes(self.total_bytes)
        self.msg_queue.put(("status", "归档完成"))
        self.msg_queue.put(("curfile", "-"))
        self.msg_queue.put(("log", "\n===== 归档全部完成 ====="))
        self.msg_queue.put(("log", f"总扫描文件：{total}"))
        self.msg_queue.put(("log", f"新复制文件：{self.copy_count}"))
        self.msg_queue.put(("log", f"跳过已存在：{self.skip_count}"))
        self.msg_queue.put(("log", f"复制数据量：{size_text}"))

        self.is_running = False
        self.msg_queue.put(("btn", "normal", "normal"))

    def start_task(self):
        if self.is_running:
            return
        t = threading.Thread(target=self.task_thread, daemon=True)
        t.start()

if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    win = tk.Tk()
    app = DjiArchiveGui(win)
    win.mainloop()
