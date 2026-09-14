# -*- coding: utf-8 -*-
"""
DJI Action5 Pro 视频归档工具 v2.0
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
        base_path = os.path.dirname(os.path.abspath(__file__))
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
# 延时摄影文件夹名正则：001_0004，捕获4位seq
TL_FOLDER_PATTERN = re.compile(r"^001_(\d{4})$")
# 大疆视频扩展名白名单，排除 .THM 缩略图 / .LRV 低分辨率预览 / .SRT 字幕 等非视频文件
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
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
        win_w = 960
        win_h = 680
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x_pos = (screen_w - win_w) // 2
        y_pos = (screen_h - win_h) // 2
        self.root.geometry(f"{win_w}x{win_h}+{x_pos}+{y_pos}")
        self.root.resizable(False, False)
        ico_path = get_resource_path("dji.ico")
        self.root.iconbitmap(ico_path)

        # ---------- 业务状态变量 ----------
        self.file_list = []         # 存放扫描出来待拷贝的文件列表 [(源路径,目标路径,目标目录,文件名,seq_no),...]
        self.copy_count = 0         # 已经成功复制的文件计数
        self.skip_count = 0         # 因为目标已存在跳过的文件计数
        self.total_bytes = 0        # 累计已经复制的总字节数，用于计算MB/GB
        self.is_running = False     # 任务运行标记，True代表正在拷贝，防止重复启动任务

        # tkinter线程安全消息队列：子线程把UI更新指令丢进队列，主线程poll_msg来消费执行
        self.msg_queue = queue.Queue()

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
        style.configure("TButton", font=("Microsoft YaHei", 9), padding=6)
        style.configure("Accent.TButton", background="#E63946", foreground="#FFFFFF", borderwidth=0, focuscolor="none")
        style.map("Accent.TButton", background=[("active", "#D62E3A")])
        style.configure("Cancel.TButton", background="#FF9800", foreground="#FFFFFF", borderwidth=0, focuscolor="none")
        style.map("Cancel.TButton", background=[("active", "#F57C00")])
        style.configure("Normal.TButton", background="#FFFFFF", foreground="#1D2129", borderwidth=1, focuscolor="none")
        style.map("Normal.TButton", background=[("active", "#F5F5F5")])
        root.configure(bg="#FFFFFF")

        # ---------- 顶部标题栏 ----------
        top_bar = ttk.Frame(root, style="TFrame")
        top_bar.pack(fill=tk.X, padx=24, pady=(18, 12))
        ttk.Label(top_bar, text="DJI Action5 Pro 视频归档工具", style="Header.TLabel").pack(side=tk.LEFT)

        # ---------- 主容器 ----------
        main_frame = ttk.Frame(root, style="TFrame")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=0)

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
        stats_grid = ttk.Frame(stat_group, style="Group.TFrame")
        stats_grid.pack(fill=tk.X)
        self._stat_item(stats_grid, "总文件", self.var_total_files, 0, 0)
        self._stat_item(stats_grid, "已复制", self.var_copied, 0, 1)
        self._stat_item(stats_grid, "已跳过", self.var_skipped, 1, 0)
        self._stat_item(stats_grid, "数据量", self.var_total_size, 1, 1)

        # 【实时速度分组】
        speed_group = ttk.Frame(left_panel, style="Group.TFrame")
        speed_group.pack(fill=tk.X, pady=(0, 16))
        ttk.Label(speed_group, text="实时速度", style="GroupTitle.TLabel").pack(anchor="w")
        ttk.Separator(speed_group, orient="horizontal").pack(fill=tk.X, pady=(10, 12))
        self.var_speed = tk.StringVar(value="0.00 MB/s")
        ttk.Label(speed_group, textvariable=self.var_speed, style="Value.TLabel").pack(anchor="w", pady=(0, 4))
        self.var_percent_single = tk.StringVar(value="单文件 0.00%")
        ttk.Label(speed_group, textvariable=self.var_percent_single, style="Desc.TLabel").pack(anchor="w")

        # 【按钮组：开始归档 + 取消 + 退出】
        btn_group = ttk.Frame(left_panel, style="Group.TFrame")
        btn_group.pack(fill=tk.X, pady=(0, 16))
        self.btn_start = ttk.Button(btn_group, text="开始归档", style="Accent.TButton", command=self.start_task, width=18)
        self.btn_start.pack(fill=tk.X, pady=(0, 6))
        self.btn_cancel = ttk.Button(btn_group, text="取消归档", style="Cancel.TButton", command=self.cancel_task, width=18, state="disabled")
        self.btn_cancel.pack(fill=tk.X, pady=(0, 6))
        self.btn_exit = ttk.Button(btn_group, text="退出", style="Normal.TButton", command=root.destroy, width=18)
        self.btn_exit.pack(fill=tk.X)

        # 【当前状态分组】
        status_group = ttk.Frame(left_panel, style="Group.TFrame")
        status_group.pack(fill=tk.X, pady=(0, 16))
        ttk.Label(status_group, text="当前状态", style="GroupTitle.TLabel").pack(anchor="w")
        ttk.Separator(status_group, orient="horizontal").pack(fill=tk.X, pady=(10, 12))
        self.var_status = tk.StringVar(value="准备就绪")
        ttk.Label(status_group, textvariable=self.var_status, font=("Microsoft YaHei", 10, "bold"), foreground="#E63946").pack(anchor="w")

        # ========== 右侧面板 ==========
        right_panel = ttk.Frame(main_frame, style="TFrame")
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(30, 0))
        progress_group = ttk.Frame(right_panel, style="Group.TFrame")
        progress_group.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
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
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.summary_text = tk.Text(log_frame, height=12, wrap=tk.WORD, font=("Microsoft YaHei", 9),
                                     bg="#FAFAFA", fg="#1D2129", relief="flat", bd=1)
        scrollbar = ttk.Scrollbar(log_frame, command=self.summary_text.yview)
        self.summary_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.summary_text.configure(yscrollcommand=scrollbar.set)

        self.poll_msg()

    @staticmethod
    def format_bytes(b):
        """原始字节数自动格式化 MB / GB"""
        gb_val = b / (1024 ** 3)
        if gb_val >= 1.0:
            return f"{float(gb_val):.2f} GB"
        else:
            mb_val = b / (1024 ** 2)
            return f"{float(mb_val):.2f} MB"

    def _stat_item(self, parent, label, var, row, col):
        """辅助函数：快速生成统计项标签，数值固定宽度左对齐防止UI抖动"""
        frame = ttk.Frame(parent, style="Group.TFrame")
        frame.grid(row=row, column=col, sticky="ew", padx=(0, 12), pady=(0, 8))
        ttk.Label(frame, text=label, style="Small.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=var, style="Value.TLabel", width=8, anchor="w").pack(anchor="w")
        parent.grid_columnconfigure(col, weight=1)

    def poll_msg(self):
        """消息队列轮询函数【主线程执行】，每50ms触发一次"""
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
                    st, st2, st3 = args
                    self.btn_start.config(state=st)
                    self.btn_cancel.config(state=st2)
                    self.btn_exit.config(state=st3)
        except queue.Empty:
            pass
        self.root.after(50, self.poll_msg)

    def scan_files(self):
        """
        文件扫描函数：遍历SOURCE_DIRS源目录，正则匹配大疆文件名 + 扩展名白名单
        :return: list [(源路径,目标路径,目标目录,文件名,seq_no), ...]
        """
        flist = []
        for src_dir in SOURCE_DIRS:
            if not os.path.isdir(src_dir):
                continue
            for fname in os.listdir(src_dir):
                if os.path.splitext(fname)[1].lower() not in VIDEO_EXTS:
                    continue
                m = PATTERN.match(fname)
                if not m:
                    continue
                date_str = m.group(1)
                seq_no = m.group(2)
                yyyy = date_str[0:4]
                mm = date_str[4:6]
                dd = date_str[6:8]
                dest_dir = os.path.join(TARGET_ROOT, yyyy, mm, dd)
                src_f = os.path.join(src_dir, fname)
                dest_f = os.path.join(dest_dir, fname)
                flist.append((src_f, dest_f, dest_dir, fname, seq_no))
        return flist

    def copy_file_stream(self, src_path, dst_path):
        """
        分块拷贝单个文件：先写 .tmp 临时文件，完成后 os.replace 原子替换
        任何异常都会删除 .tmp 确保不留下残缺文件
        """
        file_size = os.path.getsize(src_path)
        tmp_path = dst_path + ".tmp"
        bytes_written = 0
        last_time = time.perf_counter()
        last_bytes = 0
        try:
            with open(src_path, "rb") as f_in, open(tmp_path, "wb") as f_out:
                while True:
                    if not self.is_running:
                        raise RuntimeError("cancelled")
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
                    pct_single = (bytes_written / file_size) * 100 if file_size > 0 else 100
                    self.msg_queue.put(("filebar", file_size, bytes_written, speed_mb_s, pct_single))
            os.replace(tmp_path, dst_path)
            return bytes_written
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

    def _copy_tree_with_progress(self, src_dir, dst_dir, folder_name):
        """
        手动遍历复制文件夹（替代 shutil.copytree），逐文件发日志进度
        """
        os.makedirs(dst_dir, exist_ok=True)
        files = []
        for root, dirs, filenames in os.walk(src_dir):
            for fn in filenames:
                rel = os.path.relpath(os.path.join(root, fn), src_dir)
                files.append((os.path.join(root, fn), os.path.join(dst_dir, rel)))
            for d in dirs:
                os.makedirs(os.path.join(dst_dir, os.path.relpath(os.path.join(root, d), src_dir)), exist_ok=True)
        total = len(files)
        for i, (s, d) in enumerate(files, 1):
            if not self.is_running:
                raise RuntimeError("cancelled")
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            if i % 20 == 0 or i == total:
                self.msg_queue.put(("log", f"  [{folder_name}] {i}/{total}"))

    def task_thread(self):
        """后台子线程主任务"""
        self.is_running = True
        self.msg_queue.put(("btn", "disabled", "normal", "disabled"))
        self.copy_count = 0
        self.skip_count = 0
        self.total_bytes = 0
        self.msg_queue.put(("log", "开始扫描媒体文件..."))
        self.file_list = self.scan_files()
        total = len(self.file_list)

        if total == 0:
            self.msg_queue.put(("status", "未找到媒体文件"))
            self.msg_queue.put(("log", "未找到符合 DJI 命名格式的媒体文件。"))
            self.is_running = False
            self.msg_queue.put(("btn", "normal", "disabled", "normal"))
            return

        self.msg_queue.put(("stat", total, self.copy_count, self.skip_count, self.total_bytes))
        self.msg_queue.put(("status", "归档进行中"))
        self.msg_queue.put(("log", f"共发现 {total} 个媒体文件。"))
        self.msg_queue.put(("log", "===== 阶段1：开始复制视频文件 =====\n"))
        seq_to_destdir = {}

        # --------------------------
        # 阶段1：视频文件归档
        # --------------------------
        for idx, (src_file, dest_file, dest_dir, filename, seq_no) in enumerate(self.file_list):
            if not self.is_running:
                self.msg_queue.put(("status", "已取消"))
                self.msg_queue.put(("log", "\n⚠ 用户取消，停止处理。"))
                break

            os.makedirs(dest_dir, exist_ok=True)
            self.msg_queue.put(("curfile", filename[:70]))
            total_pct = (idx + 1) / total * 100
            self.msg_queue.put(("totalbar", total, idx + 1, total_pct))

            if seq_no not in seq_to_destdir:
                seq_to_destdir[seq_no] = dest_dir

            if os.path.exists(dest_file):
                self.skip_count += 1
                self.msg_queue.put(("filebar_reset",))
                self.msg_queue.put(("log", f"跳过：{filename}"))
            else:
                try:
                    wrote = self.copy_file_stream(src_file, dest_file)
                    self.copy_count += 1
                    self.total_bytes += wrote
                    self.msg_queue.put(("log", f"复制：{filename}"))
                except RuntimeError:
                    self.msg_queue.put(("log", f"⚠ 已取消，停止：{filename}"))
                    break
                except Exception as e:
                    self.msg_queue.put(("log", f"❌ 失败：{filename} — {str(e)}"))
            self.msg_queue.put(("stat", total, self.copy_count, self.skip_count, self.total_bytes))

        # 如果用户中途取消，跳过阶段2
        if not self.is_running:
            self._finish_task(total)
            return

        # --------------------------
        # 阶段2：延时摄影文件夹
        # --------------------------
        self.msg_queue.put(("log", "\n===== 阶段2：开始处理延时摄影文件夹 ====="))
        if len(seq_to_destdir) == 0:
            self.msg_queue.put(("log", "本次无需要处理的延时摄影编号。"))
        else:
            found_tl = False
            for tl_src_root in TIMELAPSE_SOURCE_DIRS:
                if not os.path.isdir(tl_src_root):
                    continue
                for entry in os.scandir(tl_src_root):
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    folder_name = entry.name
                    m = TL_FOLDER_PATTERN.match(folder_name)
                    if not m:
                        continue
                    seq_tl = m.group(1)
                    if seq_tl in seq_to_destdir:
                        found_tl = True
                        src_tl = entry.path
                        dst_tl = os.path.join(seq_to_destdir[seq_tl], folder_name)
                        if not os.path.exists(dst_tl):
                            try:
                                self._copy_tree_with_progress(src_tl, dst_tl, folder_name)
                                self.msg_queue.put(("log", f"✅ 复制延时摄影文件夹：{folder_name}"))
                            except RuntimeError:
                                self.msg_queue.put(("log", f"⚠ 已取消，停止延时摄影：{folder_name}"))
                                break
                            except Exception as e:
                                self.msg_queue.put(("log", f"❌ 延时摄影 {folder_name} 复制失败：{str(e)}"))
                        else:
                            self.msg_queue.put(("log", f"跳过延时摄影文件夹（已存在）：{folder_name}"))
                if not self.is_running:
                    break
            if not found_tl:
                self.msg_queue.put(("log", "未找到本次编号对应的延时摄影文件夹"))

        self._finish_task(total)

    def _finish_task(self, total):
        """统一收尾：打印汇总 + 恢复按钮"""
        size_text = self.format_bytes(self.total_bytes)
        self.msg_queue.put(("status", "归档完成" if self.is_running else "已取消"))
        self.msg_queue.put(("curfile", "-"))
        self.msg_queue.put(("log", "\n===== 归档全部完成 =====" if self.is_running else "\n===== 已取消 ====="))
        self.msg_queue.put(("log", f"总扫描文件：{total}"))
        self.msg_queue.put(("log", f"新复制文件：{self.copy_count}"))
        self.msg_queue.put(("log", f"跳过已存在：{self.skip_count}"))
        self.msg_queue.put(("log", f"复制数据量：{size_text}"))
        self.is_running = False
        self.msg_queue.put(("btn", "normal", "disabled", "normal"))

    def start_task(self):
        """开始归档按钮点击回调"""
        if self.is_running:
            return
        t = threading.Thread(target=self.task_thread, daemon=True)
        t.start()

    def cancel_task(self):
        """取消归档按钮点击回调"""
        if not self.is_running:
            return
        self.is_running = False
        self.msg_queue.put(("status", "正在取消..."))
        self.msg_queue.put(("log", "收到取消请求，等待当前操作完成..."))


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    win = tk.Tk()
    app = DjiArchiveGui(win)
    win.mainloop()