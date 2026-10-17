# 六轨音频服务

- `sonic_mvp.py`：项目注册、编辑运算与导出。
- `sonic_studio.py`：历史波形与项目接口。
- `sonic_live.py`：即时试听所需音频及局部变调差分。
- `live/`：当前编辑页面。
- `studio/`：保留旧页面，为模块导入提供运行文件。

在自己的环境安装 `requirements.txt`，设置 `SONIC_ROOT`、`SONIC_MVP_ROOT`，通过 `python -m uvicorn sonic_live:app --host 127.0.0.1 --port 8001` 启动。

Demucs 权重、模型 Python 环境及音频不包含在这里。此服务不提供独立的用户鉴权；只能在受控网络中运行，经主 API 的用户权限检查后访问，不能直接公开端口。
