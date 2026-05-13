# Streamlit 网站部署说明

## 本地或服务器启动

```powershell
cd C:\apps\nano-banana-gui
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501
```

## 腾讯云安全组

在腾讯云控制台给当前 Windows Server 的安全组开放入站端口：

- 协议：TCP
- 端口：8501
- 来源：建议先填自己的公网 IP；临时测试可填 `0.0.0.0/0`

开放后访问：

```text
http://服务器公网IP:8501
```

## 配置文件

页面里保存的 API Key 和 COS 配置会写入服务器本地：

```text
config.json
```

这个文件已经在 `.gitignore` 中，不要提交到 Git 仓库。

## 当前版本边界

- 第一版不包含登录系统。
- 第一版不配置域名、HTTPS、IIS 或 Nginx 反向代理。
- 结果文件保存在服务器本地输出目录。
- 任务同步执行，暂不使用数据库、Redis 或 Celery。
