"""
Nano Banana 2 图片生成工具 - 桌面 GUI 版
使用腾讯云 COS 做图片上传，直接调用 kie.ai API
"""

import io
import json
import os
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import requests
from PIL import Image, ImageTk
from qcloud_cos import CosConfig, CosS3Client

# ========== 常量配置 ==========

# 配置文件路径（和脚本同目录）
CONFIG_FILE = Path(__file__).parent / "config.json"

# 输出目录
OUTPUT_DIR = Path(__file__).parent / "输出"

# Nano Banana 2 API 端点
NANO_CREATE_TASK_URL = "https://api.kie.ai/api/v1/jobs/createTask"
NANO_TASK_STATUS_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"

# 轮询间隔（秒）
POLL_INTERVAL_SEC = 3

# 各分辨率超时时间（秒）
TIMEOUT_BY_RESOLUTION = {
    "1K": 3 * 60,
    "2K": 5 * 60,
    "4K": 8 * 60,
}

# 各分辨率并发数
CONCURRENCY_BY_RESOLUTION = {
    "1K": 3,
    "2K": 2,
    "4K": 1,
}

# 图片压缩：最大文件大小（字节）和最大尺寸（像素）
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_IMAGE_DIMENSION = 2048

# 提示词模板
PROMPT_SCENE_LOCK = """【最重要的要求】
生成一张像在同一时间、同一地点直接拍摄的真实照片，人物必须像真实站在场景里，不能有后期合成感。

【场景锁定】
背景、环境、构图、机位、透视、主体光线保持不变。

【人物融合要求】
1. 保留参考人物的脸部、发型、体型和服装特征。
2. 人物姿势按用户要求自然调整，站位与场景比例正确。
3. 人物高光、阴影、环境反光、接触阴影、景深、边缘透明过渡必须与场景一致。
4. 整体色调统一，不能出现单独调色或比背景更清晰/更模糊的情况。

【绝对禁止】
- 贴纸感、悬浮感、白边黑边、额外光源、比例错误、边缘锯齿

最终效果：看起来就像是在这个场景中实地拍摄的照片。"""

PROMPT_TEXT_GENERATE = """【场景锁定】背景环境、构图、机位、透视保持不变。

【人物生成要求】
根据用户描述生成一个真实人物，人物与场景的光照、环境反光、接触阴影、景深、比例、边缘过渡保持一致，不能有贴图感。

最终效果：看起来就像是在这个场景中实地拍摄的照片。"""


# ========== 配置持久化 ==========

def load_config():
    """从 JSON 文件加载配置"""
    defaults = {
        "nano_api_key": "",
        "cos_secret_id": "",
        "cos_secret_key": "",
        "cos_region": "ap-guangzhou",
        "cos_bucket": "",
        "cos_upload_path": "",
        "resolution": "2K",
        "output_dir": str(Path(__file__).parent / "输出"),
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            defaults.update(saved)
        except Exception:
            pass
    return defaults


def save_config(config_dict):
    """保存配置到 JSON 文件"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ========== 腾讯云 COS 上传模块 ==========

def create_cos_client(secret_id, secret_key, region):
    """创建腾讯云 COS 客户端"""
    config = CosConfig(
        Region=region,
        SecretId=secret_id,
        SecretKey=secret_key,
    )
    return CosS3Client(config)


def compress_image(image_path):
    """压缩图片，确保不超过 MAX_IMAGE_BYTES"""
    img = Image.open(image_path)

    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    w, h = img.size
    if w > MAX_IMAGE_DIMENSION or h > MAX_IMAGE_DIMENSION:
        ratio = min(MAX_IMAGE_DIMENSION / w, MAX_IMAGE_DIMENSION / h)
        new_w = int(w * ratio)
        new_h = int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    quality = 90
    while quality >= 30:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= MAX_IMAGE_BYTES:
            buf.seek(0)
            return buf, img.size
        quality -= 10

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=30)
    buf.seek(0)
    return buf, img.size


def upload_to_cos(cos_client, bucket, region, image_path, object_key):
    """上传图片到腾讯云 COS 并返回公开 URL"""
    buf, (w, h) = compress_image(image_path)
    cos_client.put_object(
        Bucket=bucket,
        Body=buf,
        Key=object_key,
        ContentType="image/jpeg",
    )

    public_url = f"https://{bucket}.cos.{region}.myqcloud.com/{object_key}"
    return public_url, w, h


# ========== Nano API 模块 ==========

def create_nano_task(api_key, prompt, image_urls, resolution):
    """创建 Nano Banana 2 生成任务，返回 taskId"""
    payload = {
        "model": "nano-banana-2",
        "input": {
            "prompt": prompt,
            "aspect_ratio": "auto",
            "resolution": resolution,
            "output_format": "jpg",
        },
    }
    if image_urls:
        payload["input"]["image_input"] = image_urls

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = requests.post(NANO_CREATE_TASK_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != 200 or not data.get("data", {}).get("taskId"):
        raise RuntimeError(f"创建任务失败: {data.get('msg', '未知错误')}")

    return data["data"]["taskId"]


def poll_nano_task(api_key, task_id, timeout_sec, cancel_flag, log_callback):
    """轮询任务状态，返回结果 URL"""
    headers = {"Authorization": f"Bearer {api_key}"}
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout_sec:
        if cancel_flag():
            raise RuntimeError("已取消")

        attempt += 1
        resp = requests.get(
            NANO_TASK_STATUS_URL,
            params={"taskId": task_id},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 200 or not data.get("data"):
            raise RuntimeError(f"查询任务状态失败: {data.get('msg', '未知错误')}")

        task_data = data["data"]
        state = task_data.get("state", "unknown")

        log_callback(f"  轮询第 {attempt} 次，状态: {state}")

        if state == "success":
            result_json_str = task_data.get("resultJson", "")
            try:
                result_json = json.loads(result_json_str)
                result_urls = result_json.get("resultUrls", [])
                if result_urls:
                    cost_time = task_data.get("costTime")
                    if cost_time:
                        log_callback(f"  任务完成，耗时 {cost_time}ms")
                    return result_urls[0]
            except json.JSONDecodeError:
                pass
            raise RuntimeError("任务成功但无法解析结果 URL")

        if state == "fail":
            fail_msg = task_data.get("failMsg", "未知原因")
            raise RuntimeError(f"任务失败: {fail_msg}")

        time.sleep(POLL_INTERVAL_SEC)

    raise RuntimeError(f"轮询超时（{timeout_sec}秒）")


# ========== 提示词构建 ==========

def build_prompt(has_character, user_prompt):
    """根据是否有人物图拼接提示词"""
    if has_character:
        return f"{PROMPT_SCENE_LOCK}\n\n【用户要求】{user_prompt or '将人物自然融入场景中，姿势自然放松'}"
    else:
        return f"{PROMPT_TEXT_GENERATE}\n\n【人物描述】{user_prompt or '一个自然站立的人，姿势放松，表情自然'}"


# ========== 主界面 ==========

class BananaApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Nano Banana 2 图片生成工具")
        self.root.geometry("1000x820")
        self.root.minsize(900, 750)

        # 状态
        self.character_image_path = None  # 人物图本地路径
        self.scene_image_paths = []  # 场景图本地路径列表
        self.results = []  # 生成结果 URL 列表
        self.is_generating = False
        self.cancel_requested = False
        self.preview_images = {}  # 缓存 tkinter 用的缩略图

        # 加载配置
        self.config = load_config()

        # 构建界面
        self._build_ui()

        # 填充已保存的配置
        self._restore_config()

    # ---------- 界面构建 ----------

    def _build_ui(self):
        """构建整个 GUI 界面"""
        # 主滚动区域
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 使用 PanedWindow 分上下两部分：操作区 和 日志区
        paned = ttk.PanedWindow(main_frame, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # 上半部分：操作区
        top_frame = ttk.Frame(paned)
        paned.add(top_frame, weight=3)

        # 下半部分：日志区
        bottom_frame = ttk.LabelFrame(paned, text="运行日志", padding=5)
        paned.add(bottom_frame, weight=1)

        self._build_top_area(top_frame)
        self._build_log_area(bottom_frame)

    def _build_top_area(self, parent):
        """构建上半部分操作区"""
        # 用 Canvas + Scrollbar 实现滚动
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        self.scroll_frame = ttk.Frame(canvas)

        self.scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # 鼠标滚轮绑定
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
        """配置区域"""
        frame = ttk.LabelFrame(parent, text="配置", padding=10)
        frame.pack(fill=tk.X, pady=(0, 10))

        # 第一行：Nano API Key + 分辨率
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

        # 输出目录
        out_frame = ttk.LabelFrame(frame, text="输出目录", padding=5)
        out_frame.pack(fill=tk.X, pady=(8, 0))

        out_row = ttk.Frame(out_frame)
        out_row.pack(fill=tk.X)

        self.output_dir_var = tk.StringVar()
        ttk.Entry(out_row, textvariable=self.output_dir_var, width=60).pack(
            side=tk.LEFT, padx=(0, 5), fill=tk.X, expand=True
        )
        ttk.Button(out_row, text="浏览...", command=self._select_output_dir).pack(side=tk.LEFT)

        # 保存配置按钮
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn_row, text="保存配置", command=self._save_config).pack(side=tk.RIGHT)

    def _build_input_section(self, parent):
        """输入区域：描述 + 图片上传"""
        frame = ttk.LabelFrame(parent, text="输入", padding=10)
        frame.pack(fill=tk.X, pady=(0, 10))

        # 描述输入
        prompt_frame = ttk.Frame(frame)
        prompt_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(prompt_frame, text="描述:").pack(anchor=tk.W)
        self.prompt_text = tk.Text(prompt_frame, height=4, wrap=tk.WORD)
        self.prompt_text.pack(fill=tk.X, pady=(4, 0))
        self.prompt_text.insert(
            "1.0", ""
        )

        hint_label = ttk.Label(
            prompt_frame,
            text="提示：有场景图+人物图时描述姿势；只有场景图时描述要生成的人物",
            foreground="gray",
        )
        hint_label.pack(anchor=tk.W)

        # 图片上传区域：左右布局
        img_frame = ttk.Frame(frame)
        img_frame.pack(fill=tk.X)

        # 左侧：人物图
        char_frame = ttk.LabelFrame(img_frame, text="人物图（可选，1张）", padding=5)
        char_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        char_btn_row = ttk.Frame(char_frame)
        char_btn_row.pack(fill=tk.X)
        ttk.Button(char_btn_row, text="选择人物图", command=self._select_character).pack(
            side=tk.LEFT
        )
        self.char_clear_btn = ttk.Button(
            char_btn_row, text="清除", command=self._clear_character, state=tk.DISABLED
        )
        self.char_clear_btn.pack(side=tk.LEFT, padx=5)
        self.char_label = ttk.Label(char_frame, text="未选择", foreground="gray")
        self.char_label.pack(anchor=tk.W, pady=4)

        # 人物图预览
        self.char_preview_label = ttk.Label(char_frame)
        self.char_preview_label.pack()

        # 右侧：场景图
        scene_frame = ttk.LabelFrame(img_frame, text="场景图（多张）", padding=5)
        scene_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))

        scene_btn_row = ttk.Frame(scene_frame)
        scene_btn_row.pack(fill=tk.X)
        ttk.Button(scene_btn_row, text="添加场景图", command=self._add_scenes).pack(
            side=tk.LEFT
        )
        self.scene_clear_btn = ttk.Button(
            scene_btn_row, text="清空全部", command=self._clear_scenes, state=tk.DISABLED
        )
        self.scene_clear_btn.pack(side=tk.LEFT, padx=5)
        self.scene_count_label = ttk.Label(scene_frame, text="已选 0 张", foreground="gray")
        self.scene_count_label.pack(anchor=tk.W, pady=4)

        # 场景图预览（横向滚动）
        scene_preview_frame = ttk.Frame(scene_frame)
        scene_preview_frame.pack(fill=tk.X)

        self.scene_canvas = tk.Canvas(scene_preview_frame, height=90, highlightthickness=0)
        self.scene_scrollbar = ttk.Scrollbar(
            scene_preview_frame, orient=tk.HORIZONTAL, command=self.scene_canvas.xview
        )
        self.scene_inner = ttk.Frame(self.scene_canvas)

        self.scene_inner.bind(
            "<Configure>",
            lambda e: self.scene_canvas.configure(scrollregion=self.scene_canvas.bbox("all")),
        )
        self.scene_canvas.create_window((0, 0), window=self.scene_inner, anchor="nw")
        self.scene_canvas.configure(xscrollcommand=self.scene_scrollbar.set)

        self.scene_canvas.pack(fill=tk.X)
        self.scene_scrollbar.pack(fill=tk.X)

    def _build_action_section(self, parent):
        """生成按钮和进度"""
        frame = ttk.Frame(parent, padding=(0, 5))
        frame.pack(fill=tk.X, pady=(0, 10))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack()

        self.generate_btn = ttk.Button(
            btn_frame, text="生成组图", command=self._on_generate, style="Accent.TButton"
        )
        self.generate_btn.pack(side=tk.LEFT, padx=5)

        self.cancel_btn = ttk.Button(
            btn_frame, text="取消", command=self._on_cancel, state=tk.DISABLED
        )
        self.cancel_btn.pack(side=tk.LEFT, padx=5)

        # 进度条
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            frame, variable=self.progress_var, maximum=100
        )
        self.progress_bar.pack(fill=tk.X, pady=(8, 0))

        self.status_label = ttk.Label(frame, text="准备就绪", foreground="gray")
        self.status_label.pack(anchor=tk.W)

    def _build_result_section(self, parent):
        """结果展示区域"""
        frame = ttk.LabelFrame(parent, text="生成结果", padding=10)
        frame.pack(fill=tk.X, pady=(0, 10))

        # 结果图片网格
        self.result_frame = ttk.Frame(frame)
        self.result_frame.pack(fill=tk.X)

        self.result_empty_label = ttk.Label(
            self.result_frame, text="等待生成...", foreground="gray"
        )
        self.result_empty_label.pack()

        # 下载按钮
        self.download_btn = ttk.Button(
            frame, text="打开输出文件夹", command=self._open_output_folder, state=tk.DISABLED
        )
        self.download_btn.pack(pady=(8, 0))

    def _build_log_area(self, parent):
        """日志区域"""
        self.log_text = tk.Text(parent, height=8, wrap=tk.WORD, state=tk.DISABLED)
        log_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 日志标签样式
        self.log_text.tag_configure("info", foreground="#4a9eff")
        self.log_text.tag_configure("success", foreground="#4caf50")
        self.log_text.tag_configure("warning", foreground="#ff9800")
        self.log_text.tag_configure("error", foreground="#f44336")

    # ---------- 配置管理 ----------

    def _restore_config(self):
        """从加载的配置恢复界面"""
        self.nano_key_var.set(self.config.get("nano_api_key", ""))
        self.cos_id_var.set(self.config.get("cos_secret_id", ""))
        self.cos_key_var.set(self.config.get("cos_secret_key", ""))
        self.cos_region_var.set(self.config.get("cos_region", "ap-guangzhou"))
        self.cos_bucket_var.set(self.config.get("cos_bucket", ""))
        self.cos_upload_path_var.set(self.config.get("cos_upload_path", ""))
        self.resolution_var.set(self.config.get("resolution", "2K"))
        self.output_dir_var.set(self.config.get("output_dir", str(OUTPUT_DIR)))

    def _save_config(self):
        """保存当前界面配置到文件"""
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
        """获取当前界面上的配置值"""
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
        """弹出文件夹选择对话框"""
        current = self.output_dir_var.get().strip()
        initial_dir = current if current and os.path.isdir(current) else str(Path.home())
        folder = filedialog.askdirectory(title="选择输出目录", initialdir=initial_dir)
        if folder:
            self.output_dir_var.set(folder)
            self._log("info", f"输出目录已更改: {folder}")

    # ---------- 图片选择 ----------

    def _select_character(self):
        """选择人物图"""
        path = filedialog.askopenfilename(
            title="选择人物图",
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.webp"), ("所有文件", "*.*")],
        )
        if path:
            self.character_image_path = path
            filename = os.path.basename(path)
            self.char_label.config(text=filename, foreground="black")
            self.char_clear_btn.config(state=tk.NORMAL)
            self._show_char_preview(path)
            self._log("info", f"已选择人物图: {filename}")

    def _clear_character(self):
        """清除人物图"""
        self.character_image_path = None
        self.char_label.config(text="未选择", foreground="gray")
        self.char_clear_btn.config(state=tk.DISABLED)
        self.char_preview_label.config(image="")
        self.preview_images.pop("character", None)
        self._log("info", "已清除人物图")

    def _show_char_preview(self, path):
        """显示人物图缩略图"""
        try:
            img = Image.open(path)
            img.thumbnail((120, 120), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.preview_images["character"] = photo
            self.char_preview_label.config(image=photo)
        except Exception:
            pass

    def _add_scenes(self):
        """添加场景图（支持多选）"""
        paths = filedialog.askopenfilenames(
            title="选择场景图（可多选）",
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.webp"), ("所有文件", "*.*")],
        )
        if paths:
            for p in paths:
                if p not in self.scene_image_paths:
                    self.scene_image_paths.append(p)
            self._update_scene_display()
            self._log("info", f"已添加 {len(paths)} 张场景图，共 {len(self.scene_image_paths)} 张")

    def _clear_scenes(self):
        """清空所有场景图"""
        self.scene_image_paths.clear()
        self._update_scene_display()
        self._log("info", "已清空全部场景图")

    def _remove_scene(self, index):
        """删除单张场景图"""
        if 0 <= index < len(self.scene_image_paths):
            removed = os.path.basename(self.scene_image_paths[index])
            del self.scene_image_paths[index]
            self._update_scene_display()
            self._log("info", f"已移除场景图: {removed}")

    def _update_scene_display(self):
        """更新场景图预览和计数"""
        count = len(self.scene_image_paths)
        self.scene_count_label.config(text=f"已选 {count} 张")
        self.scene_clear_btn.config(state=tk.NORMAL if count > 0 else tk.DISABLED)

        # 清空旧预览
        for widget in self.scene_inner.winfo_children():
            widget.destroy()

        # 清除缓存的场景预览图
        keys_to_remove = [k for k in self.preview_images if k.startswith("scene_")]
        for k in keys_to_remove:
            del self.preview_images[k]

        # 生成新的缩略图
        for i, path in enumerate(self.scene_image_paths):
            try:
                img = Image.open(path)
                img.thumbnail((75, 75), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                cache_key = f"scene_{i}"
                self.preview_images[cache_key] = photo

                item_frame = ttk.Frame(self.scene_inner)
                item_frame.pack(side=tk.LEFT, padx=2)

                label = ttk.Label(item_frame, image=photo)
                label.pack()

                # 序号
                num_label = ttk.Label(item_frame, text=f"#{i + 1}", foreground="gray")
                num_label.pack()

                # 右键删除
                idx = i
                label.bind("<Button-3>", lambda e, idx=idx: self._remove_scene(idx))
            except Exception:
                pass

    # ---------- 日志 ----------

    def _log(self, level, message):
        """添加日志（线程安全）"""
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
        """更新状态标签（线程安全）"""
        self.root.after(0, lambda: self.status_label.config(text=text))

    def _update_progress(self, value):
        """更新进度条（线程安全）"""
        self.root.after(0, lambda: self.progress_var.set(value))

    # ---------- 生成逻辑 ----------

    def _validate_inputs(self):
        """校验输入是否完整"""
        cfg = self._get_current_config()

        if not cfg["nano_api_key"]:
            return "请输入 Nano API Key"
        if not self.scene_image_paths:
            return "请至少添加一张场景图"
        return None

    def _on_generate(self):
        """点击生成按钮"""
        error = self._validate_inputs()
        if error:
            messagebox.showwarning("校验失败", error)
            return

        self.is_generating = True
        self.cancel_requested = False
        self.results.clear()
        self.generate_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self._update_progress(0)

        # 清空结果区
        for widget in self.result_frame.winfo_children():
            widget.destroy()
        self.result_empty_label = ttk.Label(
            self.result_frame, text="生成中...", foreground="gray"
        )
        self.result_empty_label.pack()

        # 在子线程执行生成
        thread = threading.Thread(target=self._generate_worker, daemon=True)
        thread.start()

    def _on_cancel(self):
        """点击取消按钮"""
        self.cancel_requested = True
        self.cancel_btn.config(state=tk.DISABLED)
        self._log("warning", "已请求取消，等待当前任务结束...")

    def _generate_worker(self):
        """子线程：执行生成流程"""
        cfg = self._get_current_config()
        api_key = cfg["nano_api_key"]
        resolution = cfg["resolution"]
        user_prompt = self.prompt_text.get("1.0", tk.END).strip()
        has_character = self.character_image_path is not None
        scene_count = len(self.scene_image_paths)
        concurrency = CONCURRENCY_BY_RESOLUTION.get(resolution, 2)
        timeout_sec = TIMEOUT_BY_RESOLUTION.get(resolution, 300)

        # 生成输出目录
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base_output = cfg.get("output_dir", "").strip()
        if not base_output:
            base_output = str(OUTPUT_DIR)
        output_dir = Path(base_output) / timestamp
        output_dir.mkdir(parents=True, exist_ok=True)

        self._log("info", f"开始生成任务: {scene_count} 张场景图, 分辨率 {resolution}")
        self._log("info", f"并发数: {concurrency}, 超时: {timeout_sec}秒")
        self._log("info", f"输出目录: {output_dir}")
        self._update_status(f"正在准备... (0/{scene_count})")

        try:
            # 创建腾讯云 COS 客户端
            cos_client = create_cos_client(
                cfg["cos_secret_id"], cfg["cos_secret_key"], cfg["cos_region"]
            )
            bucket = cfg["cos_bucket"]
            region = cfg["cos_region"]
            upload_path = cfg.get("cos_upload_path", "").strip("/")
            path_prefix = f"{upload_path}/banana-uploads" if upload_path else "banana-uploads"

            # 上传人物图（如果有）
            character_url = None
            if has_character:
                self._log("info", "正在上传人物图到 COS...")
                obj_key = f"{path_prefix}/{timestamp}/character.jpg"
                character_url, w, h = upload_to_cos(
                    cos_client, bucket, region, self.character_image_path, obj_key
                )
                self._log("success", f"人物图上传完成 ({w}x{h})")

            # 批量处理场景图
            completed = 0
            success_count = 0
            fail_count = 0

            import concurrent.futures

            # 定义单张场景处理函数
            def process_scene(scene_index, scene_path):
                if self.cancel_requested:
                    return None

                idx = scene_index + 1
                filename = os.path.basename(scene_path)
                self._log("info", f"[场景 {idx}/{scene_count}] 开始处理: {filename}")

                # 上传场景图到 COS
                self._log("info", f"[场景 {idx}] 上传场景图到 COS...")
                obj_key = f"{path_prefix}/{timestamp}/scene-{idx}.jpg"
                scene_url, w, h = upload_to_cos(
                    cos_client, bucket, region, scene_path, obj_key
                )
                self._log("success", f"[场景 {idx}] 场景图上传完成 ({w}x{h})")

                # 构建提示词
                prompt = build_prompt(has_character, user_prompt)

                # 组装图片 URL
                image_urls = []
                if character_url:
                    image_urls.append(character_url)
                image_urls.append(scene_url)

                # 创建任务
                self._log("info", f"[场景 {idx}] 创建生成任务...")
                task_id = create_nano_task(api_key, prompt, image_urls, resolution)
                self._log("info", f"[场景 {idx}] 任务已创建: {task_id}")

                # 轮询结果
                self._log("info", f"[场景 {idx}] 等待生成结果...")
                result_url = poll_nano_task(
                    api_key,
                    task_id,
                    timeout_sec,
                    lambda: self.cancel_requested,
                    lambda msg: self._log("info", f"[场景 {idx}] {msg}"),
                )

                self._log("success", f"[场景 {idx}] 生成成功!")

                # 下载结果到本地
                output_path = output_dir / f"result-{idx}.jpg"
                self._log("info", f"[场景 {idx}] 下载结果图片...")
                resp = requests.get(result_url, timeout=60)
                resp.raise_for_status()
                output_path.write_bytes(resp.content)
                self._log("success", f"[场景 {idx}] 已保存到: {output_path.name}")

                return {"url": result_url, "local_path": str(output_path), "index": scene_index}

            # 使用线程池控制并发
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                future_to_index = {}
                for i, path in enumerate(self.scene_image_paths):
                    future = executor.submit(process_scene, i, path)
                    future_to_index[future] = i

                for future in concurrent.futures.as_completed(future_to_index):
                    idx = future_to_index[future]
                    completed += 1
                    progress = (completed / scene_count) * 100
                    self._update_progress(progress)
                    self._update_status(f"已完成 {completed}/{scene_count}")

                    try:
                        result = future.result()
                        if result:
                            self.results.append(result)
                            success_count += 1
                            # 在主线程更新结果展示
                            self.root.after(0, self._refresh_results)
                    except Exception as e:
                        fail_count += 1
                        self._log("error", f"[场景 {idx + 1}] 失败: {e}")

            # 完成
            self._log("info", "=" * 50)
            if success_count > 0 and fail_count == 0:
                self._log("success", f"全部完成! 成功 {success_count} 张")
                self._update_status(f"全部完成 ({success_count} 张)")
            elif success_count > 0:
                self._log("warning", f"部分完成: 成功 {success_count} 张，失败 {fail_count} 张")
                self._update_status(f"完成 (成功{success_count}/失败{fail_count})")
            else:
                self._log("error", "全部失败，请检查配置和日志")
                self._update_status("生成失败")

            if success_count > 0:
                self._log("info", f"输出目录: {output_dir}")
                self.root.after(0, lambda: self.download_btn.config(state=tk.NORMAL))

        except Exception as e:
            self._log("error", f"生成过程出错: {e}")
            self._update_status("出错")

        finally:
            self.is_generating = False
            self.root.after(0, lambda: self.generate_btn.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.cancel_btn.config(state=tk.DISABLED))

    # ---------- 结果展示 ----------

    def _refresh_results(self):
        """刷新结果区域显示"""
        # 清空
        for widget in self.result_frame.winfo_children():
            widget.destroy()

        if not self.results:
            ttk.Label(self.result_frame, text="暂无结果", foreground="gray").pack()
            return

        # 排序
        sorted_results = sorted(self.results, key=lambda r: r["index"])

        for result in sorted_results:
            local_path = result.get("local_path")
            idx = result["index"]
            cache_key = f"result_{idx}"

            item_frame = ttk.Frame(self.result_frame)
            item_frame.pack(side=tk.LEFT, padx=4, pady=4)

            try:
                img = Image.open(local_path)
                img.thumbnail((150, 150), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.preview_images[cache_key] = photo

                img_label = ttk.Label(item_frame, image=photo, cursor="hand2")
                img_label.pack()

                # 点击打开大图预览
                path = local_path
                img_label.bind("<Button-1>", lambda e, p=path: self._preview_image(p))
            except Exception:
                ttk.Label(item_frame, text="[加载失败]").pack()

            ttk.Label(item_frame, text=f"#{idx + 1}", foreground="gray").pack()

    def _preview_image(self, image_path):
        """弹出大图预览窗口"""
        preview_win = tk.Toplevel(self.root)
        preview_win.title("图片预览")
        preview_win.geometry("800x800")

        try:
            img = Image.open(image_path)

            # 根据窗口大小缩放
            display_w, display_h = 780, 740
            img_w, img_h = img.size
            ratio = min(display_w / img_w, display_h / img_h, 1.0)
            new_w = int(img_w * ratio)
            new_h = int(img_h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)

            photo = ImageTk.PhotoImage(img)
            # 保存引用防止被垃圾回收
            preview_win._photo = photo

            canvas = tk.Canvas(preview_win, width=new_w, height=new_h)
            canvas.pack(expand=True)
            canvas.create_image(new_w // 2, new_h // 2, image=photo)

            # 底部信息和按钮
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

        # Esc 关闭
        preview_win.bind("<Escape>", lambda e: preview_win.destroy())

    def _open_output_folder(self):
        """打开输出文件夹"""
        if self.results:
            first_result = sorted(self.results, key=lambda r: r["index"])[0]
            folder = os.path.dirname(first_result["local_path"])
            os.startfile(folder)
        else:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(OUTPUT_DIR))


# ========== 启动入口 ==========

def main():
    root = tk.Tk()

    # 设置 DPI 感知（Windows 高分屏适配）
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # 设置默认字体
    default_font = ("Microsoft YaHei UI", 9)
    root.option_add("*Font", default_font)

    app = BananaApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
