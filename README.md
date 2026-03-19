# Nano Banana 2 图片生成工具（桌面版）

Python + tkinter 桌面 GUI 应用，调用 kie.ai 的 Nano Banana 2 模型生成图片。

## 功能

- 上传人物图（可选，1张）+ 多张场景图 → 批量生成组图
- 自动拼接提示词（场景锁定 + 人物融合）
- 图片自动压缩后上传到腾讯云 COS
- 批量并发控制（2K 并发2，4K 并发1）
- 生成结果自动下载到本地
- 实时日志面板
- 配置持久化（下次打开自动加载）

## 安装

```bash
cd newpython
pip install -r requirements.txt
```

需要 Python 3.8+，tkinter 已内置无需安装。

## 运行

```bash
python banana_gui.py
```

## 配置

首次使用需要填写以下配置（填写后点"保存配置"，下次自动加载）：

### Nano API Key

1. 访问 https://kie.ai/api-key
2. 注册/登录后获取 API Key

### 腾讯云 COS 存储

需要一个已开启公开访问的 COS 存储桶：

1. **SecretId / SecretKey**: 腾讯云访问管理 CAM → API 密钥管理
2. **Region**: 存储桶所在地域，例如 `ap-guangzhou`
3. **Bucket**: COS 存储桶名称，例如 `mybucket-1250000000`
4. **上传路径前缀**: 可选，表示桶内子目录前缀

程序会自动按以下格式生成公开访问地址：

`https://{bucket}.cos.{region}.myqcloud.com/{object_key}`

## 使用方法

1. 填写配置并保存
2. （可选）选择一张人物图
3. 添加一张或多张场景图
4. 输入描述文字
5. 选择分辨率
6. 点击"生成组图"
7. 等待生成完成，结果自动保存在 `输出/` 文件夹下

## 输出

生成的图片保存在脚本同目录的 `输出/年月日-时分秒/` 文件夹中。
