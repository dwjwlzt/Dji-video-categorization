# -*- coding: utf-8 -*-
"""
DJI Action5 Pro 视频归档工具 v2.1 (CustomTkinter UI)
业务逻辑与 v2.0 完全一致，仅 UI 现代化重写（红色主题）

打包命令（在当前目录下执行）：
    pyinstaller --noconfirm --onefile --windowed ^
      --icon "dji.ico" ^
      --name "视频归类v2.1新ui" ^
      --add-data "dji.ico;." ^
      --add-data "dji.png;." ^
      "视频归类v2.1新ui.py"
"""
import ctypes
import os
import re
import time
import threading
import queue
import sys
import shutil
import customtkinter as ctk

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")


def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


SOURCE_DIRS = [
    r"E:\DCIM\DJI_001",
    r"F:\DCIM\DJI_001"
]
TIMELAPSE_SOURCE_DIRS = [
    r"E:\DCIM\TIMELAPSE",
    r"F:\DCIM\TIMELAPSE"
]
TARGET_ROOT = r"G:\Osmo action5 pro"
PATTERN = re.compile(r"^DJI_(\d{8})\d{6}_(\d{4})_[A-Z]")
TL_FOLDER_PATTERN = re.compile(r"^001_(\d{4})$")
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
BLOCK_SIZE = 1024 * 1024 * 4

RED_PRIMARY = "#E63946"
RED_PRIMARY_HOVER = "#D62E3A"
ORANGE_CANCEL = "#FF9800"
ORANGE_CANCEL_HOVER = "#F57C00"


class DjiArchiveGui(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("DJI Action5 Pro 视频归档工具")
        win_w, win_h = 960, 720
        hwnd_desk = ctypes.windll.user32.GetDesktopWindow()
        hdc = ctypes.windll.user32.GetDC(hwnd_desk)
        dpi_scale = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88) / 96.0
        ctypes.windll.user32.ReleaseDC(hwnd_desk, hdc)
        phys_ww = int(win_w * dpi_scale)
        phys_wh = int(win_h * dpi_scale)
        phys_sw = ctypes.windll.user32.GetSystemMetrics(0)
        phys_sh = ctypes.windll.user32.GetSystemMetrics(1)
        x = (phys_sw - phys_ww) // 2
        y = (phys_sh - phys_wh) // 2
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")
        self.minsize(win_w, win_h)
        try:
            self.iconbitmap(get_resource_path("dji.ico"))
        except Exception:
            pass

        self.file_list = []
        self.copy_count = 0
        self.skip_count = 0
        self.total_bytes = 0
        self.is_running = False
        self.msg_queue = queue.Queue()

        self._build_ui()
        self.poll_msg()

    # ─────────────────────────────── UI 构建 ───────────────────────────────

    def _build_ui(self):
        pad = 20
        gap = 12

        top_bar = ctk.CTkFrame(self, fg_color="transparent")
        top_bar.pack(fill=ctk.X, padx=pad, pady=(pad, gap))
        ctk.CTkLabel(
            top_bar, text="DJI Action5 Pro 视频归档工具",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side=ctk.LEFT)
        ctk.CTkLabel(
            top_bar, text="自动归档 · 分块拷贝 · 延时摄影支持",
            font=ctk.CTkFont(size=12), text_color="gray",
        ).pack(side=ctk.LEFT, padx=(10, 0), pady=(6, 0))

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill=ctk.BOTH, expand=True, padx=pad, pady=(0, 12))
        main.grid_columnconfigure(0, weight=0, minsize=200)
        main.grid_columnconfigure(1, weight=1)

        self._build_left(main)
        self._build_right(main)

    def _section_title(self, parent, text):
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkFrame(parent, height=1, fg_color=("#E5E7EB", "#374151")).pack(fill=ctk.X, pady=(6, 10))

    def _build_left(self, parent):
        left = ctk.CTkFrame(parent, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 18))

        self._section_title(left, "归档统计")
        stat_card = ctk.CTkFrame(left, corner_radius=10)
        stat_card.pack(fill=ctk.X, pady=(0, 16))

        self.var_total_files = ctk.StringVar(value="0")
        self.var_copied = ctk.StringVar(value="0")
        self.var_skipped = ctk.StringVar(value="0")
        self.var_total_size = ctk.StringVar(value="0.00 MB")

        stats = [
            ("总文件", self.var_total_files),
            ("已复制", self.var_copied),
            ("已跳过", self.var_skipped),
            ("数据量", self.var_total_size),
        ]
        for i, (label, var) in enumerate(stats):
            row, col = divmod(i, 2)
            cell = ctk.CTkFrame(stat_card, fg_color="transparent")
            cell.grid(row=row, column=col, sticky="nsew", padx=14, pady=10)
            ctk.CTkLabel(
                cell, text=label,
                font=ctk.CTkFont(size=11), text_color="gray",
            ).pack(anchor="w")
            ctk.CTkLabel(
                cell, textvariable=var, width=8, anchor="w",
                font=ctk.CTkFont(size=20, weight="bold"),
                text_color=RED_PRIMARY if label == "已复制" else None,
            ).pack(anchor="w")
            stat_card.grid_columnconfigure(col, weight=1)

        self._section_title(left, "实时速度")
        speed_card = ctk.CTkFrame(left, corner_radius=10)
        speed_card.pack(fill=ctk.X, pady=(0, 16))

        self.var_speed = ctk.StringVar(value="0.00 MB/s")
        self.var_percent_single = ctk.StringVar(value="单文件 0.00%")
        ctk.CTkLabel(
            speed_card, textvariable=self.var_speed,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=RED_PRIMARY,
        ).pack(anchor="w", padx=14, pady=(12, 2))
        ctk.CTkLabel(
            speed_card, textvariable=self.var_percent_single,
            font=ctk.CTkFont(size=11), text_color="gray",
        ).pack(anchor="w", padx=14, pady=(0, 12))

        self._section_title(left, "操作")
        self.btn_start = ctk.CTkButton(
            left, text="▶  开始归档", command=self.start_task,
            height=42, corner_radius=10, hover=True,
            fg_color=RED_PRIMARY, hover_color=RED_PRIMARY_HOVER,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.btn_start.pack(fill=ctk.X, pady=(0, 6))

        self.btn_cancel = ctk.CTkButton(
            left, text="■  取消归档", command=self.cancel_task,
            height=42, corner_radius=10, hover=True, state=ctk.DISABLED,
            fg_color=ORANGE_CANCEL, hover_color=ORANGE_CANCEL_HOVER,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.btn_cancel.pack(fill=ctk.X, pady=(0, 6))

        self.btn_exit = ctk.CTkButton(
            left, text="✕  退出", command=self.destroy,
            height=42, corner_radius=10, hover=True,
            fg_color=("#D1D5DB", "#4B5563"), hover_color=("#9CA3AF", "#374151"),
            text_color=("#1D2129", "#FFFFFF"),
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.btn_exit.pack(fill=ctk.X)

        self._section_title(left, "当前状态")
        status_card = ctk.CTkFrame(left, corner_radius=10)
        status_card.pack(fill=ctk.X)
        self.var_status = ctk.StringVar(value="准备就绪")
        ctk.CTkLabel(
            status_card, textvariable=self.var_status,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=RED_PRIMARY,
        ).pack(anchor="w", padx=14, pady=12)

    def _build_right(self, parent):
        right = ctk.CTkFrame(parent)
        right.grid(row=0, column=1, sticky="nsew")

        inner = ctk.CTkFrame(right, fg_color="transparent")
        inner.pack(fill=ctk.BOTH, expand=True, padx=18, pady=18)

        self._section_title(inner, "处理进度")

        self.var_cur_file = ctk.StringVar(value="-")
        ctk.CTkLabel(
            inner, textvariable=self.var_cur_file,
            font=ctk.CTkFont(size=12), text_color="gray",
        ).pack(anchor="w", pady=(0, 14))

        ctk.CTkLabel(
            inner, text="总进度",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(anchor="w")
        self.progress_total = ctk.CTkProgressBar(
            inner, height=14, corner_radius=7,
            fg_color=("#E5E7EB", "#374151"),
            progress_color=RED_PRIMARY,
        )
        self.progress_total.set(0)
        self.progress_total.pack(fill=ctk.X, pady=(6, 4))
        self.var_percent_total = ctk.StringVar(value="0.00%")
        ctk.CTkLabel(
            inner, textvariable=self.var_percent_total,
            font=ctk.CTkFont(size=11), text_color="gray",
        ).pack(anchor="w", pady=(0, 14))

        ctk.CTkLabel(
            inner, text="单文件进度",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(anchor="w")
        self.progress_single = ctk.CTkProgressBar(
            inner, height=10, corner_radius=5,
            fg_color=("#E5E7EB", "#374151"),
            progress_color=("#FCA5A5", "#991B1B"),
        )
        self.progress_single.set(0)
        self.progress_single.pack(fill=ctk.X, pady=(6, 18))

        ctk.CTkLabel(
            inner, text="运行日志",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(anchor="w", pady=(6, 6))

        self.text_log = ctk.CTkTextbox(
            inner, font=ctk.CTkFont(family="Consolas", size=12),
            corner_radius=10, wrap="word", height=180,
        )
        self.text_log.pack(fill=ctk.BOTH, expand=True)

    # ─────────────────────────────── 业务逻辑（与 v2.0 完全一致） ───────────────────────────────

    @staticmethod
    def format_bytes(b):
        gb_val = b / (1024 ** 3)
        if gb_val >= 1.0:
            return f"{float(gb_val):.2f} GB"
        else:
            mb_val = b / (1024 ** 2)
            return f"{float(mb_val):.2f} MB"

    def poll_msg(self):
        try:
            while True:
                typ, *args = self.msg_queue.get_nowait()
                if typ == "log":
                    self.text_log.insert(ctk.END, args[0] + "\n")
                    self.text_log.see(ctk.END)
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
                    self.progress_total.set(val / maxv if maxv > 0 else 0)
                    self.var_percent_total.set(f"{pct:.2f}%")
                elif typ == "filebar":
                    maxv, val, sp, pct = args
                    self.progress_single.set(val / maxv if maxv > 0 else 0)
                    self.var_percent_single.set(f"单文件 {pct:.1f}%")
                    self.var_speed.set(f"{sp:.2f} MB/s")
                elif typ == "filebar_reset":
                    self.progress_single.set(0)
                    self.var_percent_single.set("单文件 0.00%")
                    self.var_speed.set("0.00 MB/s")
                elif typ == "btn":
                    st, st2, st3 = args
                    self.btn_start.configure(state=st)
                    self.btn_cancel.configure(state=st2)
                    self.btn_exit.configure(state=st3)
        except queue.Empty:
            pass
        self.after(50, self.poll_msg)

    def scan_files(self):
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

        if not self.is_running:
            self._finish_task(total)
            return

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
        if self.is_running:
            return
        t = threading.Thread(target=self.task_thread, daemon=True)
        t.start()

    def cancel_task(self):
        if not self.is_running:
            return
        self.is_running = False
        self.msg_queue.put(("status", "正在取消..."))
        self.msg_queue.put(("log", "收到取消请求，等待当前操作完成..."))


if __name__ == "__main__":
    app = DjiArchiveGui()
    app.mainloop()