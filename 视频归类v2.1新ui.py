# -*- coding: utf-8 -*-
"""
DJI Action5 Pro 视频归档工具 v2.1 (CustomTkinter UI)
业务逻辑基于 v2.0，并增加了异常恢复、完整性校验、进度修正和跨日期延时摄影处理。

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
import traceback
from datetime import datetime
from tkinter import messagebox
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
        self.fail_count = 0
        self.scanned_total = 0
        self.total_bytes = 0
        self.is_running = False
        self.msg_queue = queue.Queue()
        self._task_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self.worker = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
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
                self.msg_queue.put(("log", f"提示：源目录不存在，已跳过：{src_dir}"))
                continue
            try:
                with os.scandir(src_dir) as entries:
                    for entry in entries:
                        try:
                            if not entry.is_file():
                                continue
                        except OSError:
                            continue
                        fname = entry.name
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
                        flist.append((
                            entry.path,
                            os.path.join(dest_dir, fname),
                            dest_dir,
                            fname,
                            seq_no,
                            date_str,
                        ))
            except OSError as e:
                self.msg_queue.put(("log", f"❌ 无法读取源目录 {src_dir}：{e}"))
        return flist

    def scan_timelapse_folders(self):
        folders = []
        for tl_root in TIMELAPSE_SOURCE_DIRS:
            if not os.path.isdir(tl_root):
                self.msg_queue.put(("log", f"提示：延时摄影目录不存在，已跳过：{tl_root}"))
                continue
            try:
                with os.scandir(tl_root) as entries:
                    for entry in entries:
                        try:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                        except OSError:
                            continue
                        m = TL_FOLDER_PATTERN.match(entry.name)
                        if not m:
                            continue
                        folders.append((
                            entry.path,
                            entry.name,
                            m.group(1),
                            self._count_files(entry.path),
                        ))
            except OSError as e:
                self.msg_queue.put(("log", f"❌ 无法读取延时摄影目录 {tl_root}：{e}"))
        return folders

    @staticmethod
    def _count_files(src_dir):
        total = 0
        try:
            for _root, _dirs, filenames in os.walk(src_dir):
                total += len(filenames)
        except OSError:
            pass
        return total

    @staticmethod
    def _files_equal(src_path, dst_path):
        try:
            return os.path.isfile(dst_path) and os.path.getsize(src_path) == os.path.getsize(dst_path)
        except OSError:
            return False

    def _get_timelapse_dates(self, src_dir):
        dates = set()
        try:
            for root, _dirs, filenames in os.walk(src_dir):
                for name in filenames:
                    path = os.path.join(root, name)
                    try:
                        stamp = os.stat(path).st_mtime
                        dt = datetime.fromtimestamp(stamp)
                        if 2000 <= dt.year <= datetime.now().year + 1:
                            dates.add(dt.strftime("%Y%m%d"))
                    except (OSError, OverflowError, ValueError):
                        continue
        except OSError:
            pass
        return dates

    def _resolve_timelapse_dest(self, seq_no, seq_to_destdirs, src_dir):
        candidates = seq_to_destdirs.get(seq_no, {})
        dates = self._get_timelapse_dates(src_dir)

        if candidates:
            matched = sorted(set(candidates) & dates)
            if matched:
                if len(matched) > 1:
                    self.msg_queue.put(("log", f"⚠ 延时摄影日期存在多个匹配 {matched}，选用最早日期 {matched[0]}。"))
                return candidates[matched[0]]

            if dates:
                chosen = sorted(dates)[0]
                self.msg_queue.put(("log", f"⚠ 延时摄影文件日期 {chosen} 与视频序号日期 {sorted(candidates)} 不一致，按文件日期归档。"))
                return os.path.join(TARGET_ROOT, chosen[:4], chosen[4:6], chosen[6:8])

            if len(candidates) == 1:
                return next(iter(candidates.values()))

            self.msg_queue.put(("log", f"⚠ 延时摄影无法确定日期，候选日期为 {sorted(candidates)}，已跳过。"))
            return None

        if dates:
            chosen = sorted(dates)[0]
            self.msg_queue.put(("log", f"提示：未找到对应视频，按延时摄影文件时间归档到 {chosen}。"))
            return os.path.join(TARGET_ROOT, chosen[:4], chosen[4:6], chosen[6:8])

        self.msg_queue.put(("log", f"⚠ 无法确定延时摄影 {src_dir} 的归档日期，已跳过。"))
        return None

    def copy_file_stream(self, src_path, dst_path):
        file_size = os.path.getsize(src_path)
        tmp_path = dst_path + ".tmp"
        bytes_written = 0
        last_time = time.perf_counter()
        last_bytes = 0
        try:
            with open(src_path, "rb") as f_in, open(tmp_path, "wb") as f_out:
                while True:
                    if self._cancel_event.is_set():
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

        def _walk_error(error):
            raise error

        for root, dirs, filenames in os.walk(src_dir, onerror=_walk_error):
            for d in dirs:
                rel_dir = os.path.relpath(os.path.join(root, d), src_dir)
                os.makedirs(os.path.join(dst_dir, rel_dir), exist_ok=True)
            for fn in filenames:
                src = os.path.join(root, fn)
                rel = os.path.relpath(src, src_dir)
                files.append((src, os.path.join(dst_dir, rel), rel))

        total = len(files)
        copied = 0
        skipped = 0
        bytes_copied = 0
        failed = 0

        for i, (src, dst, rel) in enumerate(files, 1):
            if self._cancel_event.is_set():
                raise RuntimeError("cancelled")
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if self._files_equal(src, dst):
                    skipped += 1
                    self.msg_queue.put(("filebar_reset",))
                else:
                    self.msg_queue.put(("curfile", f"{folder_name}/{rel}"[:70]))
                    self.msg_queue.put(("filebar_reset",))
                    wrote = self.copy_file_stream(src, dst)
                    try:
                        shutil.copystat(src, dst)
                    except OSError:
                        pass
                    copied += 1
                    bytes_copied += wrote
            except RuntimeError:
                raise
            except Exception as e:
                failed += 1
                self.msg_queue.put(("log", f"❌ 延时摄影文件失败：{rel} — {e}"))
                self.msg_queue.put(("filebar_reset",))

            if i % 20 == 0 or i == total:
                self.msg_queue.put(("log", f"  [{folder_name}] {i}/{total}"))

        return copied, skipped, bytes_copied, failed

    def _run_archive(self):
        self.copy_count = 0
        self.skip_count = 0
        self.fail_count = 0
        self.scanned_total = 0
        self.total_bytes = 0
        self.msg_queue.put(("curfile", "-"))
        self.msg_queue.put(("filebar_reset",))
        self.msg_queue.put(("totalbar", 1, 0, 0.0))
        self.msg_queue.put(("log", "开始扫描媒体文件..."))

        video_list = self.scan_files()
        tl_folders = self.scan_timelapse_folders()
        total_files = len(video_list) + sum(item[3] for item in tl_folders)
        total_units = len(video_list) + len(tl_folders)
        self.scanned_total = total_files

        if total_units == 0:
            self.msg_queue.put(("log", "未找到符合 DJI 命名格式的媒体文件或延时摄影文件夹。"))
            return 0

        if not os.path.isdir(TARGET_ROOT):
            try:
                os.makedirs(TARGET_ROOT, exist_ok=True)
            except OSError as e:
                raise RuntimeError(f"无法创建归档目录 {TARGET_ROOT}：{e}") from e

        self.msg_queue.put(("stat", total_files, 0, 0, 0))
        self.msg_queue.put(("status", "归档进行中"))
        self.msg_queue.put(("totalbar", total_units, 0, 0.0))
        self.msg_queue.put((
            "log",
            f"共发现视频 {len(video_list)} 个、延时摄影文件夹 {len(tl_folders)} 个、文件 {total_files} 个。",
        ))
        self.msg_queue.put(("log", "===== 阶段1：开始复制视频文件 =====\n"))

        seq_to_destdirs = {}
        completed_units = 0

        for src_file, dest_file, dest_dir, filename, seq_no, date_str in video_list:
            if self._cancel_event.is_set():
                self.msg_queue.put(("log", "\n⚠ 用户取消，停止处理。"))
                return total_files

            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError as e:
                self.fail_count += 1
                self.msg_queue.put(("log", f"❌ 无法创建目标目录 {dest_dir}：{e}"))
                completed_units += 1
                self.msg_queue.put(("stat", total_files, self.copy_count, self.skip_count, self.total_bytes))
                self.msg_queue.put(("totalbar", total_units, completed_units, completed_units / total_units * 100))
                continue

            seq_to_destdirs.setdefault(seq_no, {})[date_str] = dest_dir
            self.msg_queue.put(("curfile", filename[:70]))
            self.msg_queue.put(("filebar_reset",))

            if self._files_equal(src_file, dest_file):
                self.skip_count += 1
                self.msg_queue.put(("log", f"跳过：{filename}"))
            else:
                if os.path.exists(dest_file):
                    self.msg_queue.put(("log", f"目标文件大小不一致，重新复制：{filename}"))
                try:
                    wrote = self.copy_file_stream(src_file, dest_file)
                    try:
                        shutil.copystat(src_file, dest_file)
                    except OSError:
                        pass
                    self.copy_count += 1
                    self.total_bytes += wrote
                    self.msg_queue.put(("log", f"复制：{filename}"))
                except RuntimeError:
                    self.msg_queue.put(("log", f"⚠ 已取消，停止：{filename}"))
                    return total_files
                except Exception as e:
                    self.fail_count += 1
                    self.msg_queue.put(("log", f"❌ 失败：{filename} — {e}"))
                    self.msg_queue.put(("filebar_reset",))

            completed_units += 1
            self.msg_queue.put(("stat", total_files, self.copy_count, self.skip_count, self.total_bytes))
            self.msg_queue.put(("totalbar", total_units, completed_units, completed_units / total_units * 100))

        if self._cancel_event.is_set():
            return total_files

        self.msg_queue.put(("log", "\n===== 阶段2：开始处理延时摄影文件夹 ====="))
        if not tl_folders:
            self.msg_queue.put(("log", "本次未发现延时摄影文件夹。"))
        else:
            for src_tl, folder_name, seq_tl, tl_file_count in tl_folders:
                if self._cancel_event.is_set():
                    self.msg_queue.put(("log", "\n⚠ 用户取消，停止处理延时摄影。"))
                    return total_files

                dest_dir = self._resolve_timelapse_dest(seq_tl, seq_to_destdirs, src_tl)
                if dest_dir is None:
                    self.fail_count += 1
                    completed_units += 1
                    self.msg_queue.put(("stat", total_files, self.copy_count, self.skip_count, self.total_bytes))
                    self.msg_queue.put(("totalbar", total_units, completed_units, completed_units / total_units * 100))
                    continue

                dst_tl = os.path.join(dest_dir, folder_name)
                self.msg_queue.put(("curfile", folder_name[:70]))
                try:
                    copied, skipped, bytes_copied, failed = self._copy_tree_with_progress(
                        src_tl, dst_tl, folder_name
                    )
                    self.copy_count += copied
                    self.skip_count += skipped
                    self.total_bytes += bytes_copied
                    self.fail_count += failed
                    if failed:
                        self.msg_queue.put((
                            "log",
                            f"⚠ 延时摄影完成但有失败：{folder_name}（复制 {copied}，跳过 {skipped}，失败 {failed}）",
                        ))
                    elif copied == 0 and skipped == tl_file_count:
                        self.msg_queue.put(("log", f"跳过延时摄影文件夹（已完整）：{folder_name}"))
                    else:
                        self.msg_queue.put(("log", f"延时摄影完成：{folder_name}（复制 {copied}，跳过 {skipped}）"))
                except RuntimeError:
                    self.msg_queue.put(("log", f"⚠ 已取消，停止延时摄影：{folder_name}"))
                    return total_files
                except Exception as e:
                    self.fail_count += 1
                    self.msg_queue.put(("log", f"❌ 延时摄影 {folder_name} 复制失败：{e}"))

                completed_units += 1
                self.msg_queue.put(("stat", total_files, self.copy_count, self.skip_count, self.total_bytes))
                self.msg_queue.put(("totalbar", total_units, completed_units, completed_units / total_units * 100))

        return total_files

    def task_thread(self):
        total_files = 0
        error = False
        try:
            total_files = self._run_archive()
        except Exception as e:
            error = True
            total_files = self.scanned_total
            self.fail_count += 1
            self.msg_queue.put(("log", f"❌ 任务异常：{e}"))
            self.msg_queue.put(("log", traceback.format_exc()))
        finally:
            self._finish_task(total_files, error=error)

    def _finish_task(self, total, error=False):
        cancelled = self._cancel_event.is_set()
        if error:
            status = "任务失败"
        elif cancelled:
            status = "已取消"
        elif total == 0:
            status = "未找到媒体文件"
        elif self.fail_count > 0:
            status = "完成（有失败）"
        else:
            status = "归档完成"

        size_text = self.format_bytes(self.total_bytes)
        self.msg_queue.put(("status", status))
        self.msg_queue.put(("curfile", "-"))
        self.msg_queue.put(("filebar_reset",))

        if error:
            self.msg_queue.put(("log", "\n===== 任务异常结束 ====="))
        elif cancelled:
            self.msg_queue.put(("log", "\n===== 已取消 ====="))
        elif total == 0:
            self.msg_queue.put(("log", "\n===== 未找到可归档文件 ====="))
        else:
            self.msg_queue.put(("log", "\n===== 归档全部完成 ====="))

        self.msg_queue.put(("log", f"总文件：{total}"))
        self.msg_queue.put(("log", f"新复制文件：{self.copy_count}"))
        self.msg_queue.put(("log", f"跳过已存在/已完整：{self.skip_count}"))
        self.msg_queue.put(("log", f"失败数量：{self.fail_count}"))
        self.msg_queue.put(("log", f"复制数据量：{size_text}"))

        with self._task_lock:
            self.is_running = False
        self.msg_queue.put(("btn", "normal", "disabled", "normal"))

    def start_task(self):
        with self._task_lock:
            if self.is_running:
                return
            self.is_running = True
            self._cancel_event.clear()

        self.msg_queue.put(("btn", "disabled", "normal", "disabled"))
        try:
            self.worker = threading.Thread(target=self.task_thread, daemon=True)
            self.worker.start()
        except Exception as e:
            with self._task_lock:
                self.is_running = False
            self.msg_queue.put(("status", "启动失败"))
            self.msg_queue.put(("log", f"❌ 无法启动任务：{e}"))
            self.msg_queue.put(("btn", "normal", "disabled", "normal"))

    def cancel_task(self):
        with self._task_lock:
            if not self.is_running:
                return
        self._cancel_event.set()
        self.msg_queue.put(("status", "正在取消..."))
        self.msg_queue.put(("log", "收到取消请求，等待当前文件复制完成..."))
        self.msg_queue.put(("btn", "disabled", "disabled", "disabled"))

    def on_close(self):
        with self._task_lock:
            running = self.is_running
        if running:
            if not messagebox.askyesno("确认退出", "归档任务正在运行，确定要取消并退出吗？"):
                return
            self._cancel_event.set()
            self.msg_queue.put(("status", "正在取消..."))
            self.msg_queue.put(("log", "收到退出请求，正在停止任务..."))
            if self.worker is not None and self.worker.is_alive():
                self.worker.join(timeout=3.0)
        self.destroy()


if __name__ == "__main__":
    app = DjiArchiveGui()
    app.mainloop()
