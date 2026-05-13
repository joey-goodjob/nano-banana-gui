from __future__ import annotations

import concurrent.futures
import io
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import requests
from PIL import Image
from qcloud_cos import CosConfig, CosS3Client


BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
OUTPUT_DIR = BASE_DIR / "输出"

VERSION = "1.1.0-web"

MODEL_NANO = "Nano Banana 2"
MODEL_GPT_IMAGE_2 = "GPT Image 2"
GPT_IMAGE_2_TEXT_MODEL = "gpt-image-2-text-to-image"
GPT_IMAGE_2_IMAGE_MODEL = "gpt-image-2-image-to-image"
GPT_IMAGE_2_ASPECT_RATIOS = ["auto", "1:1", "9:16", "16:9", "4:3", "3:4"]
MAX_GPT_IMAGE_2_INPUTS = 16

NANO_CREATE_TASK_URL = "https://api.kie.ai/api/v1/jobs/createTask"
NANO_TASK_STATUS_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"

POLL_INTERVAL_SEC = 3
API_CONNECT_TIMEOUT_SEC = 10
API_READ_TIMEOUT_SEC = 60
CREATE_TASK_MAX_RETRIES = 3
POLL_STATUS_MAX_RETRIES = 3
REQUEST_RETRY_DELAY_SEC = 2

TIMEOUT_BY_RESOLUTION = {
    "1K": 3 * 60,
    "2K": 5 * 60,
    "4K": 8 * 60,
}

CONCURRENCY_BY_RESOLUTION = {
    "1K": 3,
    "2K": 2,
    "4K": 1,
}

MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_IMAGE_DIMENSION = 2048

PROMPT_SCENE_LOCK = """【最重要的要求】
生成一张像在同一时间、同一地点直接拍摄的真实照片，人物必须像真实站在场景里，不能有后期合成感。

【场景锁定】
背景、环境、构图、机位、透视、主体光线保持不变。

【人物融合要求】
1. 保留参考人物的脸部、发型、体型和服装特征。
2. 人物姿势按用户要求自然调整，站位与场景比例正确。
3. 人物高光、阴影、环境反光、接触阴影、景深、边缘透明过渡必须与场景一致。

最终效果：看起来就像是在这个场景中实地拍摄的照片。"""

PROMPT_TEXT_GENERATE = """根据用户描述生成一个真实人物，人物与场景的光照、环境反光、接触阴影、景深、比例、边缘过渡保持一致，不能有贴图感。

最终效果：看起来就像是在这个场景中实地拍摄的照片。"""


LogCallback = Callable[[str], None]
ProgressCallback = Callable[[float], None]
CancelFlag = Callable[[], bool]


@dataclass(frozen=True)
class AppConfig:
    selected_model: str
    nano_api_key: str
    gptimage2_api_key: str
    cos_secret_id: str
    cos_secret_key: str
    cos_region: str
    cos_bucket: str
    cos_upload_path: str
    resolution: str
    gptimage2_aspect_ratio: str
    output_dir: str

    @property
    def api_key(self) -> str:
        if self.selected_model == MODEL_NANO:
            return self.nano_api_key
        return self.gptimage2_api_key


@dataclass(frozen=True)
class GeneratedImage:
    index: int
    local_path: str
    result_url: str


@dataclass(frozen=True)
class GenerationResult:
    output_dir: str
    images: list[GeneratedImage]
    success_count: int
    fail_count: int


def default_config() -> dict:
    return {
        "nano_api_key": "",
        "gptimage2_api_key": "",
        "cos_secret_id": "",
        "cos_secret_key": "",
        "cos_region": "ap-guangzhou",
        "cos_bucket": "liangzai-1373119036",
        "cos_upload_path": "",
        "resolution": "2K",
        "group_mode_model": MODEL_NANO,
        "free_create_model": MODEL_NANO,
        "gptimage2_aspect_ratio": "auto",
        "output_dir": str(OUTPUT_DIR),
    }


def load_config() -> dict:
    config = default_config()
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                config.update(saved)
        except Exception:
            pass
    return config


def save_config(config_dict: dict) -> None:
    CONFIG_FILE.write_text(
        json.dumps(config_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def merge_and_save_config(config_updates: dict) -> dict:
    config = load_config()
    config.update(config_updates)
    save_config(config)
    return config


def build_app_config(raw: dict, selected_model: str) -> AppConfig:
    return AppConfig(
        selected_model=selected_model,
        nano_api_key=raw.get("nano_api_key", "").strip(),
        gptimage2_api_key=raw.get("gptimage2_api_key", "").strip(),
        cos_secret_id=raw.get("cos_secret_id", "").strip(),
        cos_secret_key=raw.get("cos_secret_key", "").strip(),
        cos_region=raw.get("cos_region", "ap-guangzhou").strip(),
        cos_bucket=raw.get("cos_bucket", "").strip(),
        cos_upload_path=raw.get("cos_upload_path", "").strip(),
        resolution=raw.get("resolution", "2K").strip(),
        gptimage2_aspect_ratio=raw.get("gptimage2_aspect_ratio", "auto").strip(),
        output_dir=raw.get("output_dir", str(OUTPUT_DIR)).strip() or str(OUTPUT_DIR),
    )


def create_cos_client(secret_id: str, secret_key: str, region: str) -> CosS3Client:
    config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key)
    return CosS3Client(config)


def compress_image(image_path: str | Path) -> tuple[io.BytesIO, tuple[int, int]]:
    img = Image.open(image_path)

    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    w, h = img.size
    if w > MAX_IMAGE_DIMENSION or h > MAX_IMAGE_DIMENSION:
        ratio = min(MAX_IMAGE_DIMENSION / w, MAX_IMAGE_DIMENSION / h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

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


def upload_to_cos(
    cos_client: CosS3Client,
    bucket: str,
    region: str,
    image_path: str | Path,
    object_key: str,
) -> tuple[str, int, int]:
    buf, (w, h) = compress_image(image_path)
    cos_client.put_object(
        Bucket=bucket,
        Body=buf,
        Key=object_key,
        ContentType="image/jpeg",
    )
    return f"https://{bucket}.cos.{region}.myqcloud.com/{object_key}", w, h


def create_nano_task(api_key: str, prompt: str, image_urls: list[str], resolution: str) -> str:
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
    return _create_task(api_key, payload, "Nano Banana 2")


def create_gpt_image_2_task(
    api_key: str,
    prompt: str,
    image_urls: list[str],
    resolution: str,
    aspect_ratio: str,
) -> str:
    payload = {
        "model": GPT_IMAGE_2_IMAGE_MODEL if image_urls else GPT_IMAGE_2_TEXT_MODEL,
        "input": {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        },
    }
    if image_urls:
        payload["input"]["input_urls"] = image_urls
    return _create_task(api_key, payload, "GPT Image 2")


def _create_task(api_key: str, payload: dict, label: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_error: Exception | None = None

    for attempt in range(1, CREATE_TASK_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                NANO_CREATE_TASK_URL,
                json=payload,
                headers=headers,
                timeout=(API_CONNECT_TIMEOUT_SEC, API_READ_TIMEOUT_SEC),
            )
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt == CREATE_TASK_MAX_RETRIES:
                raise RuntimeError(
                    f"创建 {label} 任务失败，已重试 {CREATE_TASK_MAX_RETRIES} 次: {exc}"
                ) from exc
            time.sleep(REQUEST_RETRY_DELAY_SEC)
    else:
        raise RuntimeError(f"创建 {label} 任务失败: {last_error}")

    if data.get("code") != 200 or not data.get("data", {}).get("taskId"):
        raise RuntimeError(f"创建 {label} 任务失败: {data.get('msg', '未知错误')}")
    return data["data"]["taskId"]


def poll_task(
    api_key: str,
    task_id: str,
    timeout_sec: int,
    cancel_flag: CancelFlag,
    log_callback: LogCallback,
) -> str:
    headers = {"Authorization": f"Bearer {api_key}"}
    start_time = time.time()
    attempt = 0
    consecutive_errors = 0

    while time.time() - start_time < timeout_sec:
        if cancel_flag():
            raise RuntimeError("已取消")

        try:
            resp = requests.get(
                NANO_TASK_STATUS_URL,
                params={"taskId": task_id},
                headers=headers,
                timeout=(API_CONNECT_TIMEOUT_SEC, API_READ_TIMEOUT_SEC),
            )
            resp.raise_for_status()
            data = resp.json()
            consecutive_errors = 0
        except requests.exceptions.RequestException as exc:
            consecutive_errors += 1
            log_callback(f"查询失败，正在重试 ({consecutive_errors}/{POLL_STATUS_MAX_RETRIES}): {exc}")
            if consecutive_errors >= POLL_STATUS_MAX_RETRIES:
                raise RuntimeError(
                    f"查询任务状态连续失败 {POLL_STATUS_MAX_RETRIES} 次: {exc}"
                ) from exc
            time.sleep(REQUEST_RETRY_DELAY_SEC)
            continue

        attempt += 1
        if data.get("code") != 200 or not data.get("data"):
            raise RuntimeError(f"查询任务状态失败: {data.get('msg', '未知错误')}")

        task_data = data["data"]
        state = task_data.get("state", "unknown")
        log_callback(f"轮询第 {attempt} 次，状态: {state}")

        if state == "success":
            try:
                result_json = json.loads(task_data.get("resultJson", ""))
                result_urls = result_json.get("resultUrls", [])
                if result_urls:
                    cost_time = task_data.get("costTime")
                    if cost_time:
                        log_callback(f"任务完成，耗时 {cost_time}ms")
                    return result_urls[0]
            except json.JSONDecodeError:
                pass
            raise RuntimeError("任务成功但无法解析结果 URL")

        if state == "fail":
            fail_msg = task_data.get("failMsg", "未知原因")
            fail_code = task_data.get("failCode")
            if fail_code:
                log_callback(f"失败代码: {fail_code}")
            raise RuntimeError(f"任务失败: {fail_msg}")

        time.sleep(POLL_INTERVAL_SEC)

    raise RuntimeError(f"轮询超时（{timeout_sec} 秒）")


def poll_nano_task(
    api_key: str,
    task_id: str,
    timeout_sec: int,
    cancel_flag: CancelFlag,
    log_callback: LogCallback,
) -> str:
    return poll_task(api_key, task_id, timeout_sec, cancel_flag, log_callback)


def poll_gpt_image_2_task(
    api_key: str,
    task_id: str,
    timeout_sec: int,
    cancel_flag: CancelFlag,
    log_callback: LogCallback,
) -> str:
    return poll_task(api_key, task_id, timeout_sec, cancel_flag, log_callback)


def validate_gpt_image_2_request(
    resolution: str,
    aspect_ratio: str,
    image_count: int,
) -> str | None:
    if aspect_ratio == "auto" and resolution != "1K":
        return "GPT Image 2 在比例为 auto 时只支持 1K 分辨率"
    if aspect_ratio == "1:1" and resolution == "4K":
        return "GPT Image 2 的 1:1 比例不支持 4K 分辨率"
    if image_count > MAX_GPT_IMAGE_2_INPUTS:
        return f"GPT Image 2 最多支持 {MAX_GPT_IMAGE_2_INPUTS} 张参考图"
    return None


def build_prompt(has_character: bool, user_prompt: str) -> str:
    if has_character:
        text = user_prompt or "将人物自然融入场景中，姿势自然放松"
        return f"{PROMPT_SCENE_LOCK}\n\n【用户要求】{text}"
    text = user_prompt or "一个自然站立的人，姿势放松，表情自然"
    return f"{PROMPT_TEXT_GENERATE}\n\n【人物描述】{text}"


def validate_api_config(config: AppConfig) -> None:
    if config.selected_model == MODEL_NANO and not config.nano_api_key:
        raise ValueError("请输入 Nano API Key")
    if config.selected_model == MODEL_GPT_IMAGE_2 and not config.gptimage2_api_key:
        raise ValueError("请输入 GPT Image 2 API Key")


def validate_cos_config(config: AppConfig) -> None:
    if not config.cos_secret_id:
        raise ValueError("请输入腾讯云 COS SecretId")
    if not config.cos_secret_key:
        raise ValueError("请输入腾讯云 COS SecretKey")
    if not config.cos_region:
        raise ValueError("请输入腾讯云 COS Region")
    if not config.cos_bucket:
        raise ValueError("请输入腾讯云 COS Bucket")


def generate_group_images(
    config: AppConfig,
    user_prompt: str,
    scene_paths: list[str],
    character_path: str | None = None,
    log_callback: LogCallback | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_flag: CancelFlag | None = None,
) -> GenerationResult:
    log = log_callback or (lambda _msg: None)
    progress = progress_callback or (lambda _value: None)
    should_cancel = cancel_flag or (lambda: False)

    validate_api_config(config)
    validate_cos_config(config)
    if not scene_paths:
        raise ValueError("请至少添加一张场景图")
    if config.selected_model == MODEL_GPT_IMAGE_2:
        if not user_prompt.strip():
            raise ValueError("请输入提示词")
        error = validate_gpt_image_2_request(
            config.resolution,
            config.gptimage2_aspect_ratio,
            2 if character_path else 1,
        )
        if error:
            raise ValueError(error)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(config.output_dir) / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    log(f"开始生成组图：{len(scene_paths)} 张场景图，模型 {config.selected_model}，分辨率 {config.resolution}")
    log(f"输出目录: {output_dir}")

    cos_client = create_cos_client(
        config.cos_secret_id,
        config.cos_secret_key,
        config.cos_region,
    )
    upload_path = config.cos_upload_path.strip("/")
    path_prefix = f"{upload_path}/banana-uploads" if upload_path else "banana-uploads"

    character_url = None
    if character_path:
        log("正在上传人物图到 COS...")
        obj_key = f"{path_prefix}/{timestamp}/character.jpg"
        character_url, w, h = upload_to_cos(
            cos_client,
            config.cos_bucket,
            config.cos_region,
            character_path,
            obj_key,
        )
        log(f"人物图上传完成 ({w}x{h})")

    results: list[GeneratedImage] = []
    success_count = 0
    fail_count = 0
    result_lock = threading.Lock()
    concurrency = CONCURRENCY_BY_RESOLUTION.get(config.resolution, 2)
    timeout_sec = TIMEOUT_BY_RESOLUTION.get(config.resolution, 300)

    def process_scene(scene_index: int, scene_path: str) -> GeneratedImage:
        if should_cancel():
            raise RuntimeError("已取消")
        idx = scene_index + 1
        log(f"[场景 {idx}] 开始处理: {os.path.basename(scene_path)}")
        obj_key = f"{path_prefix}/{timestamp}/scene-{idx}.jpg"
        scene_url, w, h = upload_to_cos(
            cos_client,
            config.cos_bucket,
            config.cos_region,
            scene_path,
            obj_key,
        )
        log(f"[场景 {idx}] 场景图上传完成 ({w}x{h})")

        prompt = (
            build_prompt(bool(character_url), user_prompt)
            if config.selected_model == MODEL_NANO
            else user_prompt
        )
        image_urls = []
        if character_url:
            image_urls.append(character_url)
        image_urls.append(scene_url)

        log(f"[场景 {idx}] 正在创建生成任务...")
        task_id = _create_generation_task(config, prompt, image_urls)
        log(f"[场景 {idx}] 任务已创建: {task_id}")

        result_url = poll_task(
            config.api_key,
            task_id,
            timeout_sec,
            should_cancel,
            lambda msg: log(f"[场景 {idx}] {msg}"),
        )
        output_path = output_dir / f"result-{idx}.jpg"
        _download_result(result_url, output_path)
        log(f"[场景 {idx}] 生成成功，已保存: {output_path.name}")
        return GeneratedImage(index=scene_index, local_path=str(output_path), result_url=result_url)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_index = {
            executor.submit(process_scene, i, path): i
            for i, path in enumerate(scene_paths)
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_to_index):
            completed += 1
            progress((completed / len(scene_paths)) * 100)
            try:
                image = future.result()
                with result_lock:
                    results.append(image)
                    success_count += 1
            except Exception as exc:
                fail_count += 1
                log(f"[场景 {future_to_index[future] + 1}] 失败: {exc}")

    results.sort(key=lambda item: item.index)
    log(f"组图生成完成：成功 {success_count} 张，失败 {fail_count} 张")
    return GenerationResult(
        output_dir=str(output_dir),
        images=results,
        success_count=success_count,
        fail_count=fail_count,
    )


def generate_free_image(
    config: AppConfig,
    prompt: str,
    reference_paths: list[str],
    log_callback: LogCallback | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_flag: CancelFlag | None = None,
) -> GenerationResult:
    log = log_callback or (lambda _msg: None)
    progress = progress_callback or (lambda _value: None)
    should_cancel = cancel_flag or (lambda: False)

    validate_api_config(config)
    if reference_paths:
        validate_cos_config(config)
    if not prompt.strip():
        raise ValueError("请输入提示词")
    if len(reference_paths) > MAX_GPT_IMAGE_2_INPUTS:
        raise ValueError(f"参考图最多只能添加 {MAX_GPT_IMAGE_2_INPUTS} 张")
    if config.selected_model == MODEL_GPT_IMAGE_2:
        error = validate_gpt_image_2_request(
            config.resolution,
            config.gptimage2_aspect_ratio,
            len(reference_paths),
        )
        if error:
            raise ValueError(error)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(config.output_dir) / "free-create" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    log(f"开始自由创作：模型 {config.selected_model}，分辨率 {config.resolution}")
    log(f"输出目录: {output_dir}")

    image_urls: list[str] = []
    if reference_paths:
        cos_client = create_cos_client(
            config.cos_secret_id,
            config.cos_secret_key,
            config.cos_region,
        )
        upload_path = config.cos_upload_path.strip("/")
        path_prefix = f"{upload_path}/free-uploads" if upload_path else "free-uploads"
        log(f"正在上传 {len(reference_paths)} 张参考图到 COS...")
        for i, ref_path in enumerate(reference_paths):
            if should_cancel():
                raise RuntimeError("已取消")
            obj_key = f"{path_prefix}/{timestamp}/ref-{i + 1}.jpg"
            url, w, h = upload_to_cos(
                cos_client,
                config.cos_bucket,
                config.cos_region,
                ref_path,
                obj_key,
            )
            image_urls.append(url)
            log(f"参考图 {i + 1} 上传完成 ({w}x{h})")
            progress(((i + 1) / max(len(reference_paths), 1)) * 25)

    task_id = _create_generation_task(config, prompt, image_urls)
    log(f"任务已创建: {task_id}")
    progress(40)

    result_url = poll_task(
        config.api_key,
        task_id,
        TIMEOUT_BY_RESOLUTION.get(config.resolution, 300),
        should_cancel,
        log,
    )
    progress(80)

    output_path = output_dir / "result.jpg"
    _download_result(result_url, output_path)
    progress(100)
    log(f"自由创作完成，已保存: {output_path}")
    return GenerationResult(
        output_dir=str(output_dir),
        images=[GeneratedImage(index=0, local_path=str(output_path), result_url=result_url)],
        success_count=1,
        fail_count=0,
    )


def _create_generation_task(config: AppConfig, prompt: str, image_urls: list[str]) -> str:
    if config.selected_model == MODEL_NANO:
        return create_nano_task(config.api_key, prompt, image_urls, config.resolution)
    return create_gpt_image_2_task(
        config.api_key,
        prompt,
        image_urls,
        config.resolution,
        config.gptimage2_aspect_ratio,
    )


def _download_result(result_url: str, output_path: Path) -> None:
    resp = requests.get(result_url, timeout=60)
    resp.raise_for_status()
    output_path.write_bytes(resp.content)


def save_uploaded_files(
    files: Iterable,
    target_dir: Path,
    prefix: str,
) -> list[str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []
    for i, uploaded_file in enumerate(files, start=1):
        name = _safe_filename(getattr(uploaded_file, "name", f"{prefix}-{i}.jpg"))
        suffix = Path(name).suffix or ".jpg"
        path = target_dir / f"{prefix}-{i}{suffix}"
        path.write_bytes(uploaded_file.getvalue())
        saved_paths.append(str(path))
    return saved_paths


def save_single_uploaded_file(uploaded_file, target_dir: Path, prefix: str) -> str | None:
    if uploaded_file is None:
        return None
    return save_uploaded_files([uploaded_file], target_dir, prefix)[0]


def _safe_filename(filename: str) -> str:
    cleaned = "".join(ch for ch in filename if ch.isalnum() or ch in ("-", "_", "."))
    return cleaned or "upload.jpg"
