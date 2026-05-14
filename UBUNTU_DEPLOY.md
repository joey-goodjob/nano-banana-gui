# Ubuntu VPS 自动部署说明

这套脚本用于把 Streamlit 网站部署到 Ubuntu VPS，并注册成 systemd 后台服务。

## 1. 上传或拉取代码

如果服务器能访问公开 GitHub 仓库：

```bash
sudo apt-get update
sudo apt-get install -y git
cd /opt
sudo git clone https://github.com/joey-goodjob/nano-banana-gui.git
sudo chown -R "$USER:$USER" /opt/nano-banana-gui
cd /opt/nano-banana-gui
```

如果仓库是私有的，可以在本地下载 ZIP 后上传到服务器，再解压到：

```text
/opt/nano-banana-gui
```

## 2. 一键部署

在项目根目录执行：

```bash
bash scripts/deploy_ubuntu.sh
```

默认配置：

- 服务名：`nano-banana-gui`
- 监听地址：`0.0.0.0`
- 端口：`8501`
- 项目目录：当前目录

如果要改端口：

```bash
PORT=8502 bash scripts/deploy_ubuntu.sh
```

## 3. 访问网站

本机测试：

```text
http://127.0.0.1:8501
```

公网访问：

```text
http://你的服务器公网IP:8501
```

VPS 控制台或云厂商安全组需要放行 `TCP 8501`。

如果服务器启用了 UFW，脚本会自动开放当前端口。

## 4. 常用管理命令

查看服务状态：

```bash
sudo systemctl status nano-banana-gui
```

查看实时日志：

```bash
sudo journalctl -u nano-banana-gui -f
```

重启服务：

```bash
sudo systemctl restart nano-banana-gui
```

停止服务：

```bash
sudo systemctl stop nano-banana-gui
```

## 5. 更新代码

如果服务器是通过 Git clone 部署的：

```bash
bash scripts/update_ubuntu.sh
```

如果服务器是 ZIP 上传部署的，重新上传新版代码后再执行：

```bash
bash scripts/deploy_ubuntu.sh
```

## 6. 配置保存

Web 页面里的 API Key 和 COS 配置会保存到当前浏览器的 localStorage，不会写入服务器本地 `config.json`。

注意：如果换浏览器、清理浏览器缓存、使用无痕模式，配置需要重新填写。
