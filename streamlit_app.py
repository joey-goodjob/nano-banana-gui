from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path
from threading import Lock

import streamlit as st

from banana_services import (
    GPT_IMAGE_2_ASPECT_RATIOS,
    MODEL_GPT_IMAGE_2,
    MODEL_NANO,
    OUTPUT_DIR,
    build_app_config,
    generate_free_image,
    generate_group_images,
    load_config,
    merge_and_save_config,
    save_single_uploaded_file,
    save_uploaded_files,
)


st.set_page_config(
    page_title="Nano Banana 图片生成工具",
    page_icon="🍌",
    layout="wide",
)


IMAGE_TYPES = ["jpg", "jpeg", "png", "webp"]


def main() -> None:
    _apply_style()
    raw_config = load_config()

    st.title("Nano Banana 图片生成工具")

    tabs = st.tabs(["组图生成", "自由创作"])
    with tabs[0]:
        _render_group_tab(raw_config)
    with tabs[1]:
        _render_free_tab(raw_config)

    with st.sidebar:
        st.subheader("服务器启动")
        st.code(
            "streamlit run streamlit_app.py "
            "--server.address 0.0.0.0 --server.port 8501",
            language="powershell",
        )
        st.caption("腾讯云安全组需要开放 8501 端口。")


def _render_group_tab(raw_config: dict) -> None:
    left, right = st.columns([0.95, 1.35], gap="large")

    with left:
        st.subheader("配置")
        group_config = _render_config_panel(raw_config, "group")

    with right:
        st.subheader("输入")
        character_file = st.file_uploader(
            "人物图，可选 1 张",
            type=IMAGE_TYPES,
            accept_multiple_files=False,
            key="group_character",
        )
        scene_files = st.file_uploader(
            "场景图，可上传多张",
            type=IMAGE_TYPES,
            accept_multiple_files=True,
            key="group_scenes",
        )
        prompt = st.text_area("描述词", height=140, key="group_prompt")

        submitted = st.button("开始生成组图", type="primary", use_container_width=True)

    progress = st.progress(0, text="等待开始")
    log_box = st.empty()

    if submitted:
        logs: list[str] = []
        log_lock = Lock()

        def log(message: str) -> None:
            with log_lock:
                logs.append(message)

        def progress_update(value: float) -> None:
            progress.progress(min(max(int(value), 0), 100), text=f"进度 {int(value)}%")

        try:
            upload_dir = OUTPUT_DIR / "web-uploads" / datetime.now().strftime("%Y%m%d-%H%M%S")
            character_path = save_single_uploaded_file(character_file, upload_dir, "character")
            scene_paths = save_uploaded_files(scene_files or [], upload_dir, "scene")
            config = build_app_config(group_config, group_config["group_mode_model"])

            with st.spinner("正在生成组图..."):
                result = generate_group_images(
                    config=config,
                    user_prompt=prompt.strip(),
                    scene_paths=scene_paths,
                    character_path=character_path,
                    log_callback=log,
                    progress_callback=progress_update,
                )

            st.session_state["group_result"] = result
            st.session_state["group_logs"] = logs
            if result.fail_count:
                st.warning(f"完成：成功 {result.success_count} 张，失败 {result.fail_count} 张。")
            else:
                st.success(f"全部完成：成功 {result.success_count} 张。")
        except Exception as exc:
            st.error(f"生成失败：{exc}")
            st.session_state["group_logs"] = logs + [f"生成失败: {exc}"]
        finally:
            log_box.code("\n".join(st.session_state.get("group_logs", logs)) or "暂无日志")

    _render_logs("group_logs")
    _render_result("group_result", "组图结果")


def _render_free_tab(raw_config: dict) -> None:
    left, right = st.columns([0.95, 1.35], gap="large")

    with left:
        st.subheader("配置")
        free_config = _render_config_panel(raw_config, "free")

    with right:
        st.subheader("输入")
        reference_files = st.file_uploader(
            "参考图，可上传多张",
            type=IMAGE_TYPES,
            accept_multiple_files=True,
            key="free_references",
        )
        prompt = st.text_area("提示词", height=180, key="free_prompt")
        submitted = st.button("开始自由创作", type="primary", use_container_width=True)

    progress = st.progress(0, text="等待开始")
    log_box = st.empty()

    if submitted:
        logs: list[str] = []

        def log(message: str) -> None:
            logs.append(message)

        def progress_update(value: float) -> None:
            progress.progress(min(max(int(value), 0), 100), text=f"进度 {int(value)}%")

        try:
            upload_dir = OUTPUT_DIR / "web-uploads" / datetime.now().strftime("%Y%m%d-%H%M%S")
            reference_paths = save_uploaded_files(reference_files or [], upload_dir, "reference")
            config = build_app_config(free_config, free_config["free_create_model"])

            with st.spinner("正在自由创作..."):
                result = generate_free_image(
                    config=config,
                    prompt=prompt.strip(),
                    reference_paths=reference_paths,
                    log_callback=log,
                    progress_callback=progress_update,
                )

            st.session_state["free_result"] = result
            st.session_state["free_logs"] = logs
            st.success("自由创作完成。")
        except Exception as exc:
            st.error(f"生成失败：{exc}")
            st.session_state["free_logs"] = logs + [f"生成失败: {exc}"]
        finally:
            log_box.code("\n".join(st.session_state.get("free_logs", logs)) or "暂无日志")

    _render_logs("free_logs")
    _render_result("free_result", "自由创作结果")


def _render_config_panel(raw_config: dict, mode: str) -> dict:
    model_key = "group_mode_model" if mode == "group" else "free_create_model"
    form_key = f"{mode}_config_form"
    model_default = raw_config.get(model_key, MODEL_NANO)
    if model_default not in [MODEL_NANO, MODEL_GPT_IMAGE_2]:
        model_default = MODEL_NANO

    with st.form(form_key):
        selected_model = st.selectbox(
            "模型",
            [MODEL_NANO, MODEL_GPT_IMAGE_2],
            index=[MODEL_NANO, MODEL_GPT_IMAGE_2].index(model_default),
            key=f"{mode}_model",
        )
        resolution = st.selectbox(
            "分辨率",
            ["1K", "2K", "4K"],
            index=_index_or_zero(["1K", "2K", "4K"], raw_config.get("resolution", "2K")),
            key=f"{mode}_resolution",
        )
        aspect_ratio = st.selectbox(
            "GPT Image 2 比例",
            GPT_IMAGE_2_ASPECT_RATIOS,
            index=_index_or_zero(
                GPT_IMAGE_2_ASPECT_RATIOS,
                raw_config.get("gptimage2_aspect_ratio", "auto"),
            ),
            key=f"{mode}_aspect_ratio",
            disabled=selected_model != MODEL_GPT_IMAGE_2,
        )

        nano_api_key = st.text_input(
            "Nano API Key",
            value=raw_config.get("nano_api_key", ""),
            type="password",
            key=f"{mode}_nano_key",
        )
        gptimage2_api_key = st.text_input(
            "GPT Image 2 API Key",
            value=raw_config.get("gptimage2_api_key", ""),
            type="password",
            key=f"{mode}_gpt_key",
        )
        cos_secret_id = st.text_input(
            "COS SecretId",
            value=raw_config.get("cos_secret_id", ""),
            type="password",
            key=f"{mode}_cos_id",
        )
        cos_secret_key = st.text_input(
            "COS SecretKey",
            value=raw_config.get("cos_secret_key", ""),
            type="password",
            key=f"{mode}_cos_key",
        )
        cos_region = st.text_input(
            "COS Region",
            value=raw_config.get("cos_region", "ap-guangzhou"),
            key=f"{mode}_cos_region",
        )
        cos_bucket = st.text_input(
            "COS Bucket",
            value=raw_config.get("cos_bucket", ""),
            key=f"{mode}_cos_bucket",
        )
        cos_upload_path = st.text_input(
            "上传路径前缀",
            value=raw_config.get("cos_upload_path", ""),
            key=f"{mode}_cos_path",
        )
        output_dir = st.text_input(
            "输出目录",
            value=raw_config.get("output_dir", str(OUTPUT_DIR)),
            key=f"{mode}_output_dir",
        )
        saved = st.form_submit_button("保存配置", use_container_width=True)

    config = {
        "nano_api_key": nano_api_key.strip(),
        "gptimage2_api_key": gptimage2_api_key.strip(),
        "cos_secret_id": cos_secret_id.strip(),
        "cos_secret_key": cos_secret_key.strip(),
        "cos_region": cos_region.strip(),
        "cos_bucket": cos_bucket.strip(),
        "cos_upload_path": cos_upload_path.strip(),
        "resolution": resolution,
        "gptimage2_aspect_ratio": aspect_ratio,
        "output_dir": output_dir.strip(),
        model_key: selected_model,
    }

    if saved:
        merge_and_save_config(config)
        st.success("配置已保存到服务器本地 config.json")

    return {**raw_config, **config}


def _render_logs(session_key: str) -> None:
    logs = st.session_state.get(session_key)
    if logs:
        st.subheader("运行日志")
        st.code("\n".join(logs), language="text")


def _render_result(session_key: str, title: str) -> None:
    result = st.session_state.get(session_key)
    if not result:
        return

    st.subheader(title)
    st.caption(f"输出目录：{result.output_dir}")

    if result.images:
        zip_bytes = _zip_dir(Path(result.output_dir))
        st.download_button(
            "下载全部结果",
            data=zip_bytes,
            file_name=f"{Path(result.output_dir).name}.zip",
            mime="application/zip",
            use_container_width=True,
        )

    columns = st.columns(3)
    for i, image in enumerate(result.images):
        path = Path(image.local_path)
        with columns[i % 3]:
            st.image(str(path), caption=path.name, use_container_width=True)
            st.download_button(
                "下载图片",
                data=path.read_bytes(),
                file_name=path.name,
                mime="image/jpeg",
                key=f"{session_key}_{i}_download",
                use_container_width=True,
            )


def _zip_dir(directory: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in directory.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(directory))
    buf.seek(0)
    return buf.getvalue()


def _index_or_zero(values: list[str], value: str) -> int:
    try:
        return values.index(value)
    except ValueError:
        return 0


def _apply_style() -> None:
    st.markdown(
        """
        <style>
        :root {
            --banana-ink: #1e2528;
            --banana-line: #d8e3dc;
            --banana-accent: #17a26a;
        }
        .stApp {
            background:
                linear-gradient(180deg, rgba(245, 249, 246, 0.88), rgba(255,255,255,0.95)),
                repeating-linear-gradient(90deg, rgba(23,162,106,0.05) 0 1px, transparent 1px 42px);
            color: var(--banana-ink);
        }
        section[data-testid="stSidebar"] {
            border-right: 1px solid var(--banana-line);
        }
        div[data-testid="stFileUploader"] section {
            border-color: var(--banana-line);
            border-radius: 8px;
        }
        .stButton > button,
        .stDownloadButton > button,
        .stFormSubmitButton > button {
            border-radius: 8px;
            border: 1px solid var(--banana-line);
            font-weight: 650;
        }
        .stButton > button[kind="primary"] {
            background: var(--banana-accent);
            border-color: var(--banana-accent);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
