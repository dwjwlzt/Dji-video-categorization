# -*- coding: utf-8 -*-
"""
DJI Action5 Pro 视频归档工具
功能说明：
1. 扫描多个固定源目录 E:\\DCIM\\DJI_001 、F:\\DCIM\\DJI_001
2. 使用正则匹配大疆原始命名格式的媒体文件 DJI_8位日期+6位时间_序号_字母
3. 自动解析文件名里面的日期，按照 年\\月\\日三级文件夹层级存放视频
4. 新逻辑：全部视频归档完成之后，再统一扫描处理延时摄影文件夹
5. 分块拷贝大文件，避免一次性读入内存，实时计算拷贝速度
6. 目标文件/延时文件夹已存在则直接跳过，不重复复制
7. UI界面：总进度条、单文件进度条（灰色轨道+绿色填充），统计总文件/已复制/已跳过/总数据量
8. 数据量自动单位切换：小于1GB显示MB，大于等于1GB显示GB，保留两位小数
9. 全部UI更新通过消息队列，子线程只做IO计算，避免tkinter子线程直接操作控件导致界面卡死、刷新异常
10. 运行日志输出拷贝/跳过详情，固定高度日志框，窗口底部保留空白
11. 打包命令：pyinstaller -F -w -i dji.ico --add-data "dji.ico;." 视频归类.py
配置区：修改最上方SOURCE_DIRS、TARGET_ROOT可以更换源和目标路径
"""
import os
import re
import time
import tkinter as tk
from tkinter import ttk
import threading
import queue
import sys
import shutil
import ctypes
def get_resource_path(relative_path):
    """获取资源路径，区分开发 / pyinstaller exe环境"""
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)
# ====================== 【配置区 - 需要修改路径在这里】 ======================
# 多个SD卡视频源文件夹
SOURCE_DIRS = [
    r"E:\DCIM\DJI_001",
    r"F:\DCIM\DJI_001"
]
# 延时摄影源目录
TIMELAPSE_SOURCE_DIRS = [
    r"E:\DCIM\TIMELAPSE",
    r"F:\DCIM\TIMELAPSE"
]
# 归档输出根目录，视频会按 年\月\日 三级子文件夹存放在这里
TARGET_ROOT = r"G:\Osmo action5 pro"
# 正则：捕获 8位日期(group1)，4位序列编号(group2)
# 文件名示例 DJI_20260906144919_0004_D.MP4
PATTERN = re.compile(r"^DJI_(\d{8})\d{6}_(\d{4})_[A-Z]")
# 文件拷贝块大小：4MB，分块读写大文件，防止一次性加载整个大视频进内存
BLOCK_SIZE = 1024 * 1024 * 4
# ==========================================================================
class DjiArchiveGui:
    def __init__(self, root):
        """
        程序GUI初始化构造函数
        :param root: tkinter主窗口实例
        """
        self.root = root
        self.root.title("DJI Action5 Pro 视频归档工具")
        # ==========窗口居中修改==========
        win_w = 960
        win_h = 580
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x_pos = (screen_w - win_w) // 2
        y_pos = (screen_h - win_h) // 2
        self.root.geometry(f"{win_w}x{win_h}+{x_pos}+{y_pos}")
        # ===============================
        self.root.resizable(False, False) # 禁止窗口拉伸缩放
        ico_path = get_resource_path("dji.ico")
        self.root.iconbitmap(ico_path)
        # ---------- 业务状态变量 ----------
        self.file_list = []         # 存放扫描出来待拷贝的文件列表 [(源路径,目标路径,目标目录,文件名,seq_no),...]
        self.copy_count = 0         # 已经成功复制的文件计数
        self.skip_count = 0         # 因为目标已存在跳过的文件计数
        self.total_bytes = 0        # 累计已经复制的总字节数，用于计算MB/GB
        self.is_running = False     # 任务运行标记，True代表正在拷贝，防止重复启动任务
        # tkinter线程安全消息队列：子线程把UI更新指令丢进队列，主线程poll_msg来消费执行
        # tkinter禁止子线程直接操作界面控件，全部界面动作必须交给主线程执行
        self.msg_queue = queue.Queue()
        # ---------- 界面样式初始化 ----------
        style = ttk.Style()
        style.theme_use("clam")  # 使用clam主题，可以自定义进度条颜色
        # TProgressbar全局样式：进度条轨道背景灰色，内部填充块绿色
        style.configure("TProgressbar",
                        troughcolor="#E0E0E0",   # 轨道底色（未完成部分）
                        background="#4CAF50")    # 填充颜色（已完成绿色）
        style.configure("TFrame", background="#FFFFFF")                # 普通容器背景白色
        style.configure("Group.TFrame", background="#FFFFFF")          # 分组容器样式
        style.configure("TLabel", background="#FFFFFF", foreground="#1D2129", font=("Microsoft YaHei", 9))
        style.configure("Header.TLabel", background="#FFFFFF", foreground="#1D2129", font=("Microsoft YaHei", 11, "bold"))
        style.configure("GroupTitle.TLabel", background="#FFFFFF", foreground="#1D2129", font=("Microsoft YaHei", 10, "bold"))
        style.configure("Small.TLabel", background="#FFFFFF", foreground="#86909C", font=("Microsoft YaHei", 8))
        style.configure("Value.TLabel", background="#FFFFFF", foreground="#1D2129", font=("Microsoft YaHei", 18, "bold"))
        style.configure("Desc.TLabel", background="#FFFFFF", foreground="#4E5969", font=("Microsoft YaHei", 9))
        # 按钮样式
        style.configure("TButton", font=("Microsoft YaHei", 9), padding=6)
        style.configure("Accent.TButton", background="#E63946", foreground="#FFFFFF", borderwidth=0, focuscolor="none")
        style.map("Accent.TButton", background=[("active", "#D62E3A")]) # 鼠标悬浮变红
        style.configure("Normal.TButton", background="#FFFFFF", foreground="#1D2129", borderwidth=1, focuscolor="none")
        style.map("Normal.TButton", background=[("active", "#F5F5F5")])
        root.configure(bg="#FFFFFF") # 主窗口背景白色
        # ---------- 顶部标题栏 ----------
        top_bar = ttk.Frame(root, style="TFrame")
        top_bar.pack(fill=tk.X, padx=24, pady=(18, 12))
        ttk.Label(top_bar, text="DJI Action5 Pro 视频归档工具", style="Header.TLabel").pack(side=tk.LEFT)
        # ---------- 主容器，承载左右两大块面板 ----------
        main_frame = ttk.Frame(root, style="TFrame")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=0)
        # ========== 左侧面板，固定宽度160像素 ==========
        left_panel = ttk.Frame(main_frame, style="TFrame", width=160)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, expand=False)
        # 【归档统计分组】总文件 / 已复制 / 已跳过 / 数据量
        stat_group = ttk.Frame(left_panel, style="Group.TFrame")
        stat_group.pack(fill=tk.X, pady=(0, 16))
        ttk.Label(stat_group, text="归档统计", style="GroupTitle.TLabel").pack(anchor="w")
        ttk.Separator(stat_group, orient="horizontal").pack(fill=tk.X, pady=(10, 12))
        # StringVar：tkinter绑定标签文本的变量，修改变量界面自动刷新
        self.var_total_files = tk.StringVar(value="0")
        self.var_copied = tk.StringVar(value="0")
        self.var_skipped = tk.StringVar(value="0")
        self.var_total_size = tk.StringVar(value="0.00 MB")
        stats_grid = ttk.Frame(stat_group, style="Group.TFrame")
        stats_grid.pack(fill=tk.X)
        self._stat_item(stats_grid, "总文件", self.var_total_files, 0)
        self._stat_item(stats_grid, "已复制", self.var_copied, 1)
        self._stat_item(stats_grid, "已跳过", self.var_skipped, 2)
        self._stat_item(stats_grid, "数据量", self.var_total_size, 3)
        # 【实时速度分组】拷贝MB/s、单文件百分比
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
        # 【当前状态分组】显示准备就绪 / 归档进行中 / 归档完成 / 未找到媒体文件
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
        self.var_cur_file = tk.StringVar(value="-") # 当前正在处理的文件名
        ttk.Label(progress_group, textvariable=self.var_cur_file, style="Desc.TLabel").pack(anchor="w", pady=(0, 12))
        # upper_frame：存放总进度条、单文件进度条，只占自身高度，不抢占剩余垂直空间
        upper_frame = ttk.Frame(progress_group, style="Group.TFrame")
        upper_frame.pack(fill=tk.X, expand=False)
        # 总文件进度条：统计已经处理多少个文件
        self.progress_total = ttk.Progressbar(upper_frame, style="TProgressbar", orient="horizontal", mode="determinate")
        self.progress_total.pack(fill=tk.X, pady=(0, 8))
        self.var_percent_total = tk.StringVar(value="0.00%")
        ttk.Label(upper_frame, textvariable=self.var_percent_total, style="Desc.TLabel").pack(anchor="w")
        # 单文件进度条：单个视频拷贝字节进度
        ttk.Label(upper_frame, text="单文件进度", style="Small.TLabel").pack(anchor="w", pady=(12, 4))
        self.progress_single = ttk.Progressbar(upper_frame, style="TProgressbar", orient="horizontal", mode="determinate")
        self.progress_single.pack(fill=tk.X, pady=(0, 12))
        # 运行日志区域，套log_frame容器，Text+Scrollbar组合，height=12固定显示行数
        ttk.Label(progress_group, text="运行日志", style="Small.TLabel").pack(anchor="w", pady=(12, 4))
        log_frame = ttk.Frame(progress_group)
        log_frame.pack(fill=tk.X, expand=False)
        self.summary_text = tk.Text(log_frame, height=12, wrap=tk.WORD, font=("Microsoft YaHei", 9),
                                     bg="#FAFAFA", fg="#1D2129", relief="flat", bd=1)
        scrollbar = ttk.Scrollbar(log_frame, command=self.summary_text.yview)
        self.summary_text.pack(side=tk.LEFT, fill=tk.X, expand=False)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.summary_text.configure(yscrollcommand=scrollbar.set)
        # 启动消息队列轮询，每10ms读取队列执行UI操作
        self.poll_msg()
    @staticmethod
    def format_bytes(b):
        """
        【静态工具函数】原始字节数自动格式化 MB / GB
        :param b: int 字节总数
        :return: str 格式化后字符串，例 "256.12 MB" / "12.78 GB"
        >=1GB就输出GB，否则输出MB，全部保留两位小数
        """
        gb_val = b / (1024 ** 3)
        if gb_val >= 1.0:
            return f"{float(gb_val):.2f} GB"
        else:
            mb_val = b / (1024 ** 2)
            return f"{float(mb_val):.2f} MB"
    def _stat_item(self, parent, label, var, col):
        """
        辅助函数：快速生成统计项标签（总文件/已复制等）
        :param parent: 父容器
        :param label: 小标题文字
        :param var: StringVar变量绑定数值
        :param col: grid所在列号
        """
        frame = ttk.Frame(parent, style="Group.TFrame")
        frame.grid(row=0, column=col, sticky="ew", padx=(0, 12))
        ttk.Label(frame, text=label, style="Small.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=var, style="Value.TLabel").pack(anchor="w")
        parent.grid_columnconfigure(col, weight=1)
    def poll_msg(self):
        """
        消息队列轮询函数【主线程执行】
        不停读取msg_queue里面的指令，执行界面更新；
        子线程只put数据，不碰任何tk控件，保证线程安全；
        self.root.after(10,...) 每10毫秒触发一次轮询
        """
        try:
            # 循环读取队列中所有待处理消息，直到队列为空抛出queue.Empty
            while True:
                typ, *args = self.msg_queue.get_nowait()
                if typ == "log":
                    # 写日志文本
                    self.summary_text.insert(tk.END, args[0] + "\n")
                    self.summary_text.see(tk.END) # 自动滚动到底部
                elif typ == "status":
                    # 修改状态文字
                    self.var_status.set(args[0])
                elif typ == "curfile":
                    # 修改当前文件名显示
                    self.var_cur_file.set(args[0])
                elif typ == "stat":
                    # 更新统计面板：总文件，已复制，已跳过，总字节
                    tot, cop, skip, b = args
                    self.var_total_files.set(str(tot))
                    self.var_copied.set(str(cop))
                    self.var_skipped.set(str(skip))
                    self.var_total_size.set(self.format_bytes(b))
                elif typ == "totalbar":
                    # 更新总文件进度条 (最大值，当前值，百分比)
                    maxv, val, pct = args
                    self.progress_total["maximum"] = maxv
                    self.progress_total["value"] = val
                    self.var_percent_total.set(f"{pct:.2f}%")
                elif typ == "filebar":
                    # 更新单文件拷贝进度条、拷贝速度
                    maxv, val, sp, pct = args
                    self.progress_single["maximum"] = maxv
                    self.progress_single["value"] = val
                    self.var_percent_single.set(f"单文件 {pct:.1f}%")
                    self.var_speed.set(f"{sp:.2f} MB/s")
                elif typ == "filebar_reset":
                    # 重置单文件进度条归零（文件跳过的时候调用）
                    self.progress_single["value"] = 0
                    self.var_percent_single.set("单文件 0.00%")
                    self.var_speed.set("0.00 MB/s")
                elif typ == "btn":
                    # 修改按钮状态 normal / disabled
                    st, st2 = args
                    self.btn_start.config(state=st)
                    self.btn_exit.config(state=st2)
        except queue.Empty:
            # 队列空就不处理
            pass
        # 递归调度，10毫秒后再次执行轮询
        self.root.after(10, self.poll_msg)
    def scan_files(self):
        """
        文件扫描函数：遍历SOURCE_DIRS源目录，正则匹配大疆文件名，计算目标保存路径
        :return: list 列表，每一项元组 (源文件完整路径,目标文件完整路径,目标文件夹路径,文件名,seq_no)
        seq_no：4位序列编号，用于延时摄影文件夹 001_0004
        """
        flist = []
        # 遍历每一个源目录
        for src_dir in SOURCE_DIRS:
            if not os.path.isdir(src_dir):
                continue  # 如果这个源文件夹不存在直接跳过
            # 遍历文件夹下面全部文件
            for fname in os.listdir(src_dir):
                m = PATTERN.match(fname)
                if not m:
                    continue  # 正则不匹配，不是大疆视频，跳过
                date_str = m.group(1)  # 捕获8位日期字符串，例如"20260906"
                seq_no = m.group(2)    # 4位序列编号：0004
                # date_str格式 YYYYMMDD
                yyyy = date_str[0:4]  # 完整4位年 2026
                mm = date_str[4:6]    # 两位月份 09
                dd = date_str[6:8]    # 两位日期 06
                # 三级嵌套：年\月\日
                dest_dir = os.path.join(TARGET_ROOT, yyyy, mm, dd)
                src_f = os.path.join(src_dir, fname)   # 源文件完整路径
                dest_f = os.path.join(dest_dir, fname) # 目标文件完整路径
                flist.append((src_f, dest_f, dest_dir, fname, seq_no))
        return flist
    def copy_file_stream(self, src_path, dst_path):
        """
        分块拷贝单个文件函数，子线程调用
        :param src_path: 源文件路径
        :param dst_path: 目标输出路径
        :return: int 成功写入总字节数
        边拷贝边计算瞬时拷贝速度，进度数据丢进消息队列，不直接操作UI控件
        """
        file_size = os.path.getsize(src_path) # 获取源文件总大小（字节）
        bytes_written = 0                     # 当前已经拷贝字节
        last_time = time.perf_counter()       # 上一次计时时间
        last_bytes = 0                        # 上一次记录的已拷贝字节
        # rb只读二进制；wb写二进制
        with open(src_path, "rb") as f_in, open(dst_path, "wb") as f_out:
            while True:
                chunk = f_in.read(BLOCK_SIZE) # 每次读取BLOCK_SIZE大小块
                if not chunk:
                    break # read返回空字节串代表读取文件结束，退出循环
                f_out.write(chunk)
                bytes_written += len(chunk)
                # 计算瞬时拷贝速度 MB/s
                now = time.perf_counter()
                delta_t = now - last_time
                delta_b = bytes_written - last_bytes
                speed_mb_s = (delta_b / (1024 * 1024)) / delta_t if delta_t > 0 else 0
                last_time = now
                last_bytes = bytes_written
                # 计算单文件拷贝百分比
                pct_single = (bytes_written / file_size) * 100
                # 把进度消息丢入队列，交给主线程去刷新界面
                self.msg_queue.put(("filebar", file_size, bytes_written, speed_mb_s, pct_single))
        return bytes_written
    def task_thread(self):
        """
        【后台子线程主任务】
        阶段1：扫描并归档全部视频文件；收集本次任务所有seq_no以及对应目标目录
        阶段2：视频全部完成后，再统一扫描延时摄影源目录，复制匹配编号的延时文件夹
        """
        self.is_running = True
        self.msg_queue.put(("btn", "disabled", "disabled")) # 任务运行时禁用按钮
        self.copy_count = 0
        self.skip_count = 0
        self.total_bytes = 0
        self.msg_queue.put(("log", "开始扫描媒体文件..."))
        self.file_list = self.scan_files()
        total = len(self.file_list)
        # 扫描结果为空，没有匹配的大疆视频
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
        # 字典：key=seq_no，value=该编号对应的目标存放目录，同一个seq只存一次
        seq_to_destdir = {}
        # --------------------------
        # 阶段1：只处理视频，延时逻辑全部移到后面
        # --------------------------
        for idx, (src_file, dest_file, dest_dir, filename, seq_no) in enumerate(self.file_list):
            if not self.is_running:
                break # 如果标记变成False，中止循环
            os.makedirs(dest_dir, exist_ok=True)
            self.msg_queue.put(("curfile", filename[:70]))
            total_pct = (idx + 1) / total * 100
            self.msg_queue.put(("totalbar", total, idx+1, total_pct))
            # 记录seq_no对应的目标目录，同一个seq_no只存一次
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
        # --------------------------
        # 阶段2：视频全部拷贝完毕，现在统一扫描延时摄影文件夹
        # --------------------------
        self.msg_queue.put(("log", "\n===== 阶段2：开始处理延时摄影文件夹 ====="))
        # seq_to_destdir.keys() 就是本次归档出现的全部编号
        if len(seq_to_destdir) == 0:
                self.msg_queue.put(("log", "本次无需要处理的延时摄影编号。"))
        else:
                tl_folder_pattern = re.compile(r"^001_(\d{4})$")  # 匹配文件夹名 001_0004，捕获4位seq
                found_tl = False  # 标记是否找到并处理过任意延时文件夹
                for tl_src_root in TIMELAPSE_SOURCE_DIRS:
                    if not os.path.isdir(tl_src_root):
                        continue
                    # 列出延时摄影源目录下全部子文件夹
                    for entry in os.scandir(tl_src_root):
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        folder_name = entry.name
                        m = tl_folder_pattern.match(folder_name)
                        if not m:
                            continue
                        seq_tl = m.group(1)
                        # 判断：这个延时摄影编号，是不是本次视频处理过的编号
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
                # 如果遍历完所有延时目录，一个匹配的都没找到
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
        """
        【开始归档按钮点击回调】
        不直接跑任务，新建守护子线程执行task_thread，避免UI卡死
        daemon=True 主窗口关闭子线程自动结束
        """
        if self.is_running:
            return # 任务正在运行，点击无效，防止重复启动
        t = threading.Thread(target=self.task_thread, daemon=True)
        t.start()
if __name__ == "__main__":
    # 开启Windows高DPI感知，解决文字模糊，非Windows直接跳过
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    # 程序入口，实例化tk窗口和GUI类，启动消息循环
    win = tk.Tk()
    app = DjiArchiveGui(win)
    win.mainloop()