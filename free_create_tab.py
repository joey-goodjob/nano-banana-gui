"""
自由创作 Tab - 直接调用 Nano Banana 2 API，不附加任何内置提示词
"""

import os
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import tkinter as tk

import requests
from PIL import Image, ImageTk

from banana_gui import (
    OUTPUT_DIR,
    TIMEOUT_BY_RESOLUTION,
    create_cos_client,
    create_nano_task,
    load_config,
    poll_nano_task,
    save_config,
    upload_to_cos,
)


class FreeCreateTab:
    def __init__(self, parent, root):
        self.parent = parent
        self.root = root

        self.reference_image_paths = []
        self.is_generating = False
        self.cancel_requested = False
        self.preview_images = {}
        self.result_path = None

        self.config = load_config()

        self._build_ui()
        self._restore_config()

    # ---------- 界面构建 ----------

    def _build_ui(self):
        main_frame = ttk.Frame(self.parent, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        paned = ttk.PanedWindow(main_frame, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.Frame(paned)
        paned.add(top_frame, weight=3)

        bottom_frame = ttk.LabelFrame(paned, text="运行日志", padding=5)
        paned.add(bottom_frame, weight=1)

        self._build_top_area(top_frame)
        self._build_log_area(bottom_frame)

    def _build_top_area(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        self.scroll_frame = ttk.Frame(canvas)

        self.scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._build_config_section(self.scroll_frame)
        self._build_input_section(self.scroll_frame)
        self._build_action_section(self.scroll_frame)
        self._build_result_section(self.scroll_frame)

    def _build_config_section(self, parent):
        frame = ttk.LabelFrame(parent, text="配置", padding=10)
        frame.pack(fill=tk.X, pady=(0, 10))

        row1 = ttk.Frame(frame)
        row1.pack(fill=tk.X, pady=2)

        ttk.Label(row1, text="Nano API Key:").pack(side=tk.LEFT)
        self.nano_key_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.nano_key_var, width=50, show="*").pack(
            side=tk.LEFT, padx=(5, 20)
        )

        ttk.Label(row1, text="分辨率:").pack(side=tk.LEFT)
        self.resolution_var = tk.StringVar(value="2K")
        res_combo = ttk.Combobox(
            row1,
            textvariable=self.resolution_var,
            values=["1K", "2K", "4K"],
            width=5,
            state="readonly",
        )
        res_combo.pack(side=tk.LEFT, padx=5)

        cos_frame = ttk.LabelFrame(frame, text="腾讯云 COS 存储配置", padding=5)
        cos_frame.pack(fill=tk.X, pady=(8, 0))

        cos_row1 = ttk.Frame(cos_frame)
        cos_row1.pack(fill=tk.X, pady=2)

        ttk.Label(cos_row1, text="SecretId:").pack(side=tk.LEFT)
        self.cos_id_var = tk.StringVar()
        ttk.Entry(cos_row1, textvariable=self.cos_id_var, width=40).pack(
            side=tk.LEFT, padx=(5, 15)
        )

        ttk.Label(cos_row1, text="SecretKey:").pack(side=tk.LEFT)
        self.cos_key_var = tk.StringVar()
        ttk.Entry(cos_row1, textvariable=self.cos_key_var, width=40, show="*").pack(
            side=tk.LEFT, padx=5
        )

        cos_row2 = ttk.Frame(cos_frame)
        cos_row2.pack(fill=tk.X, pady=2)

        ttk.Label(cos_row2, text="地域(Region):").pack(side=tk.LEFT)
        self.cos_region_var = tk.StringVar(value="ap-guangzhou")
        ttk.Combobox(
            cos_row2,
            textvariable=self.cos_region_var,
            values=[
                "ap-beijing", "ap-shanghai", "ap-guangzhou",
                "ap-chengdu", "ap-chongqing", "ap-nanjing",
                "ap-hongkong", "ap-singapore",
            ],
            width=15,
        ).pack(side=tk.LEFT, padx=(5, 15))

        ttk.Label(cos_row2, text="Bucket:").pack(side=tk.LEFT)
        self.cos_bucket_var = tk.StringVar()
        ttk.Entry(cos_row2, textvariable=self.cos_bucket_var, width=30).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Label(cos_row2, text="(如: mybucket-1250000000)").pack(side=tk.LEFT)

        cos_row3 = ttk.Frame(cos_frame)
        cos_row3.pack(fill=tk.X, pady=2)

        ttk.Label(cos_row3, text="上传路径前缀:").pack(side=tk.LEFT)
        self.cos_upload_path_var = tk.StringVar()
        ttk.Entry(cos_row3, textvariable=self.cos_upload_path_var, width=20).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Label(cos_row3, text="(可选，桶内的子目录)").pack(side=tk.LEFT)

        out_frame = ttk.LabelFrame(frame, text="输出目录", padding=5)
        out_frame.pack(fill=tk.X, pady=(8, 0))

        out_row = ttk.Frame(out_frame)
        out_row.pack(fill=tk.X)

        self.output_dir_var = tk.StringVar()
        ttk.Entry(out_row, textvariable=self.output_dir_var, width=60).pack(
            side=tk.LEFT, padx=(0, 5), fill=tk.X, expand=True
        )
        ttk.Button(out_row, text="浏览...", command=self._select_output_dir).pack(side=tk.LEFT)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn_row, text="保存配置", command=self._save_config).pack(side=tk.RIGHT)

    def _build_input_section(self, parent):
        frame = ttk.LabelFrame(parent, text="输入", padding=10)
        frame.pack(fill=tk.X, pady=(0, 10))

        prompt_frame = ttk.Frame(frame)
        prompt_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(prompt_frame, text="提示词（直接发送给模型，不附加任何内置提示词）:").pack(anchor=tk.W)
        self.prompt_text = tk.Text(prompt_frame, height=6, wrap=tk.WORD)
        self.prompt_text.pack(fill=tk.X, pady=(4, 0))

        hint_label = ttk.Label(
            prompt_frame,
            text="提示：你输入的内容会原封不动发送给 Banana 模型，请自行编写完整的提示词",
            foreground="gray",
        )
        hint_label.pack(anchor=tk.W)

        ref_frame = ttk.LabelFrame(frame, text="参考图（可选，0~N 张）", padding=5)
        ref_frame.pack(fill=tk.X)

        ref_btn_row = ttk.Frame(ref_frame)
        ref_btn_row.pack(fill=tk.X)

        ttk.Button(ref_btn_row, text="添加参考图", command=self._add_references).pack(
            side=tk.LEFT
        )
        self.ref_clear_btn = ttk.Button(
            ref_btn_row, text="清空全部", command=self._clear_references, state=tk.DISABLED
        )
        self.ref_clear_btn.pack(side=tk.LEFT, padx=5)
        self.ref_count_label = ttk.Label(ref_frame, text="已选 0 张", foreground="gray")
        self.ref_count_label.pack(anchor=tk.W, pady=4)

        preview_frame = ttk.Frame(ref_frame)
        preview_frame.pack(fill=tk.X)

        self.ref_canvas = tk.Canvas(preview_frame, height=90, highlightthickness=0)
        self.ref_scrollbar = ttk.Scrollbar(
            preview_frame, orient=tk.HORIZONTAL, command=self.ref_canvas.xview
        )
        self.ref_inner = ttk.Frame(self.ref_canvas)

        self.ref_inner.bind(
            "<Configure>",
            lambda e: self.ref_canvas.configure(scrollregion=self.ref_canvas.bbox("all")),
        )
        self.ref_canvas.create_window((0, 0), window=self.ref_inner, anchor="nw")
        self.ref_canvas.configure(xscrollcommand=self.ref_scrollbar.set)

        self.ref_canvas.pack(fill=tk.X)
        self.ref_scrollbar.pack(fill=tk.X)

    def _build_action_section(self, parent):
        frame = ttk.Frame(parent, padding=(0, 5))
        frame.pack(fill=tk.X, pady=(0, 10))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack()

        self.generate_btn = ttk.Button(
            btn_frame, text="生成图片", command=self._on_generate
        )
        self.generate_btn.pack(side=tk.LEFT, padx=5)

        self.cancel_btn = ttk.Button(
            btn_frame, text="取消", command=self._on_cancel, state=tk.DISABLED
        )
        self.cancel_btn.pack(side=tk.LEFT, padx=5)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            frame, variable=self.progress_var, maximum=100
        )
        self.progress_bar.pack(fill=tk.X, pady=(8, 0))

        self.status_label = ttk.Label(frame, text="准备就绪", foreground="gray")
        self.status_label.pack(anchor=tk.W)

    def _build_result_section(self, parent):
        frame = ttk.LabelFrame(parent, text="生成结果", padding=10)
        frame.pack(fill=tk.X, pady=(0, 10))

        self.result_frame = ttk.Frame(frame)
        self.result_frame.pack(fill=tk.X)

        self.result_empty_label = ttk.Label(
            self.result_frame, text="等待生成...", foreground="gray"
        )
        self.result_empty_label.pack()

        self.open_folder_btn = ttk.Button(
            frame, text="打开输出文件夹", command=self._open_output_folder, state=tk.DISABLED
        )
        self.open_folder_btn.pack(pady=(8, 0))

    def _build_log_area(self, parent):
        self.log_text = tk.Text(parent, height=8, wrap=tk.WORD, state=tk.DISABLED)
        log_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.tag_configure("info", foreground="#4a9eff")
        self.log_text.tag_configure("success", foreground="#4caf50")
        self.log_text.tag_configure("warning", foreground="#ff9800")
        self.log_text.tag_configure("error", foreground="#f44336")

    # ---------- 配置管理 ----------

    def _restore_config(self):
        self.nano_key_var.set(self.config.get("nano_api_key", ""))
        self.cos_id_var.set(self.config.get("cos_secret_id", ""))
        self.cos_key_var.set(self.config.get("cos_secret_key", ""))
        self.cos_region_var.set(self.config.get("cos_region", "ap-guangzhou"))
        self.cos_bucket_var.set(self.config.get("cos_bucket", ""))
        self.cos_upload_path_var.set(self.config.get("cos_upload_path", ""))
        self.resolution_var.set(self.config.get("resolution", "2K"))
        self.output_dir_var.set(self.config.get("output_dir", str(OUTPUT_DIR)))

    def _save_config(self):
        config = {
            "nano_api_key": self.nano_key_var.get().strip(),
            "cos_secret_id": self.cos_id_var.get().strip(),
            "cos_secret_key": self.cos_key_var.get().strip(),
            "cos_region": self.cos_region_var.get().strip(),
            "cos_bucket": self.cos_bucket_var.get().strip(),
            "cos_upload_path": self.cos_upload_path_var.get().strip(),
            "resolution": self.resolution_var.get(),
            "output_dir": self.output_dir_var.get().strip(),
        }
        save_config(config)
        self._log("success", "配置已保存")

    def _get_current_config(self):
        return {
            "nano_api_key": self.nano_key_var.get().strip(),
            "cos_secret_id": self.cos_id_var.get().strip(),
            "cos_secret_key": self.cos_key_var.get().strip(),
            "cos_region": self.cos_region_var.get().strip(),
            "cos_bucket": self.cos_bucket_var.get().strip(),
            "cos_upload_path": self.cos_upload_path_var.get().strip(),
            "resolution": self.resolution_var.get(),
            "output_dir": self.output_dir_var.get().strip(),
        }

    def _select_output_dir(self):
        current = self.output_dir_var.get().strip()
        initial_dir = current if current and os.path.isdir(current) else str(Path.home())
        folder = filedialog.askdirectory(title="选择输出目录", initialdir=initial_dir)
        if folder:
            self.output_dir_var.set(folder)
            self._log("info", f"输出目录已更改: {folder}")

    # ---------- 参考图选择 ----------

    def _add_references(self):
        paths = filedialog.askopenfilenames(
            title="选择参考图（可多选）",
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.webp"), ("所有文件", "*.*")],
        )
        if paths:
            for p in paths:
                if p not in self.reference_image_paths:
                    self.reference_image_paths.append(p)
            self._update_ref_display()
            self._log("info", f"已添加 {len(paths)} 张参考图，共 {len(self.reference_image_paths)} 张")

    def _clear_references(self):
        self.reference_image_paths.clear()
        self._update_ref_display()
        self._log("info", "已清空全部参考图")

    def _remove_reference(self, index):
        if 0 <= index < len(self.reference_image_paths):
            removed = os.path.basename(self.reference_image_paths[index])
            del self.reference_image_paths[index]
            self._update_ref_display()
            self._log("info", f"已移除参考图: {removed}")

    def _update_ref_display(self):
        count = len(self.reference_image_paths)
        self.ref_count_label.config(text=f"已选 {count} 张")
        self.ref_clear_btn.config(state=tk.NORMAL if count > 0 else tk.DISABLED)

        for widget in self.ref_inner.winfo_children():
            widget.destroy()

        keys_to_remove = [k for k in self.preview_images if k.startswith("ref_")]
        for k in keys_to_remove:
            del self.preview_images[k]

        for i, path in enumerate(self.reference_image_paths):
            try:
                img = Image.open(path)
                img.thumbnail((75, 75), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                cache_key = f"ref_{i}"
                self.preview_images[cache_key] = photo

                item_frame = ttk.Frame(self.ref_inner)
                item_frame.pack(side=tk.LEFT, padx=2)

                label = ttk.Label(item_frame, image=photo)
                label.pack()

                num_label = ttk.Label(item_frame, text=f"#{i + 1}", foreground="gray")
                num_label.pack()

                idx = i
                label.bind("<Button-3>", lambda e, idx=idx: self._remove_reference(idx))
            except Exception:
                pass

    # ---------- 日志 ----------

    def _log(self, level, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        tag = level

        def _append():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"[{timestamp}] ", "info")
            self.log_text.insert(tk.END, f"{message}\n", tag)
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)

        self.root.after(0, _append)

    def _update_status(self, text):
        self.root.after(0, lambda: self.status_label.config(text=text))

    def _update_progress(self, value):
        self.root.after(0, lambda: self.progress_var.set(value))

    # ---------- 生成逻辑 ----------

    def _on_generate(self):
        cfg = self._get_current_config()
        if not cfg["nano_api_key"]:
            messagebox.showwarning("校验失败", "请输入 Nano API Key")
            return

        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showwarning("校验失败", "请输入提示词")
            return

        self.is_generating = True
        self.cancel_requested = False
        self.result_path = None
        self.generate_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self._update_progress(0)

        for widget in self.result_frame.winfo_children():
            widget.destroy()
        self.result_empty_label = ttk.Label(
            self.result_frame, text="生成中...", foreground="gray"
        )
        self.result_empty_label.pack()

        thread = threading.Thread(target=self._generate_worker, daemon=True)
        thread.start()

    def _on_cancel(self):
        self.cancel_requested = True
        self.cancel_btn.config(state=tk.DISABLED)
        self._log("warning", "已请求取消...")

    def _generate_worker(self):
        cfg = self._get_current_config()
        api_key = cfg["nano_api_key"]
        resolution = cfg["resolution"]
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        timeout_sec = TIMEOUT_BY_RESOLUTION.get(resolution, 300)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base_output = cfg.get("output_dir", "").strip()
        if not base_output:
            base_output = str(OUTPUT_DIR)
        output_dir = Path(base_output) / "free-create" / timestamp
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            image_urls = []

            if self.reference_image_paths:
                cos_client = create_cos_client(
                    cfg["cos_secret_id"], cfg["cos_secret_key"], cfg["cos_region"]
                )
                bucket = cfg["cos_bucket"]
                region = cfg["cos_region"]
                upload_path = cfg.get("cos_upload_path", "").strip("/")
                path_prefix = f"{upload_path}/free-uploads" if upload_path else "free-uploads"

                self._log("info", f"正在上传 {len(self.reference_image_paths)} 张参考图到 COS...")

                for i, ref_path in enumerate(self.reference_image_paths):
                    if self.cancel_requested:
                        raise RuntimeError("已取消")
                    filename = os.path.basename(ref_path)
                    self._log("info", f"上传参考图 [{i + 1}]: {filename}")
                    obj_key = f"{path_prefix}/{timestamp}/ref-{i + 1}.jpg"
                    url, w, h = upload_to_cos(cos_client, bucket, region, ref_path, obj_key)
                    self._log("success", f"参考图 [{i + 1}] 上传完成 ({w}x{h})")
                    image_urls.append(url)

            self._log("info", f"正在创建任务，分辨率: {resolution}")
            self._update_status("正在创建任务...")
            self._update_progress(20)

            task_id = create_nano_task(api_key, prompt, image_urls, resolution)
            self._log("info", f"任务已创建: {task_id}")
            self._update_status("等待生成结果...")
            self._update_progress(40)

            def log_cb(msg):
                self._log("info", msg)

            result_url = poll_nano_task(
                api_key,
                task_id,
                timeout_sec,
                lambda: self.cancel_requested,
                log_cb,
            )

            self._log("success", "生成成功!")
            self._update_progress(80)

            output_path = output_dir / "result.jpg"
            self._log("info", "正在下载结果图片...")
            resp = requests.get(result_url, timeout=60)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)

            self.result_path = str(output_path)
            self._log("success", f"已保存到: {output_path}")
            self._update_progress(100)
            self._update_status("生成完成!")

            self.root.after(0, self._refresh_result)
            self.root.after(0, lambda: self.open_folder_btn.config(state=tk.NORMAL))

        except Exception as e:
            self._log("error", f"生成失败: {e}")
            self._update_status("生成失败")

        finally:
            self.is_generating = False
            self.root.after(0, lambda: self.generate_btn.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.cancel_btn.config(state=tk.DISABLED))

    # ---------- 结果展示 ----------

    def _refresh_result(self):
        for widget in self.result_frame.winfo_children():
            widget.destroy()

        if not self.result_path:
            ttk.Label(self.result_frame, text="暂无结果", foreground="gray").pack()
            return

        try:
            img = Image.open(self.result_path)
            img.thumbnail((300, 300), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.preview_images["result"] = photo

            img_label = ttk.Label(self.result_frame, image=photo, cursor="hand2")
            img_label.pack(pady=4)

            path = self.result_path
            img_label.bind("<Button-1>", lambda e, p=path: self._preview_image(p))
        except Exception:
            ttk.Label(self.result_frame, text="[加载失败]").pack()

    def _preview_image(self, image_path):
        preview_win = tk.Toplevel(self.root)
        preview_win.title("图片预览")
        preview_win.geometry("800x800")

        try:
            img = Image.open(image_path)

            display_w, display_h = 780, 740
            img_w, img_h = img.size
            ratio = min(display_w / img_w, display_h / img_h, 1.0)
            new_w = int(img_w * ratio)
            new_h = int(img_h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)

            photo = ImageTk.PhotoImage(img)
            preview_win._photo = photo

            canvas = tk.Canvas(preview_win, width=new_w, height=new_h)
            canvas.pack(expand=True)
            canvas.create_image(new_w // 2, new_h // 2, image=photo)

            info_frame = ttk.Frame(preview_win)
            info_frame.pack(fill=tk.X, pady=5)

            ttk.Label(info_frame, text=f"文件: {os.path.basename(image_path)}").pack(side=tk.LEFT, padx=10)
            ttk.Label(info_frame, text=f"原始尺寸: {img_w}x{img_h}").pack(side=tk.LEFT, padx=10)

            ttk.Button(
                info_frame,
                text="在文件管理器中打开",
                command=lambda: os.startfile(os.path.dirname(image_path)),
            ).pack(side=tk.RIGHT, padx=10)

        except Exception as e:
            ttk.Label(preview_win, text=f"无法加载图片: {e}").pack(expand=True)

        preview_win.bind("<Escape>", lambda e: preview_win.destroy())

    def _open_output_folder(self):
        if self.result_path:
            os.startfile(os.path.dirname(self.result_path))
        else:
            base = self.output_dir_var.get().strip() or str(OUTPUT_DIR)
            target = Path(base) / "free-create"
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))
