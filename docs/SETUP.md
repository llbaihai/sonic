# 安装与复现

## 运行层级

| 层级 | 需要什么 | 可以验证什么 |
| --- | --- | --- |
| 本地后端测试 | Python 3.12 与 requirements | 音频处理、任务包装、访问控制；模型边界使用测试夹具 |
| 本地技术界面 | 同上，另准备 Lucide | 上传、音高分析、本地合成、作品播放 |
| 账号与手机界面 | PostgreSQL、账号 API、自己的模型配置 | 注册登录、个人声音库、提示词与生成任务 |
| 完整歌曲与六轨 | GPU、YuE2/VAE、Demucs、GPU 音频服务 | 云端生成、六轨分离与导出 |
| 录音设备 | 自行获授权的 SDK 与内部桥服务 | 设备连接、下载与录音导入 |

后两项有独立依赖，不是安装 Python 包后自动具备的能力。

## 1. 主 API

建议先从项目根目录创建自己的虚拟环境：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r backend/requirements.txt
python scripts/prepare_icons.py
python -m pytest backend/tests -c pytest.ini
python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8770
```

Linux 需要系统 `libsndfile`，某些音频/转码流程需要 `ffmpeg`。默认技术页为 `http://127.0.0.1:8770/`，手机创作页为 `/mobile/`。没有模型/翻译服务时手机页不能完成 YuE2 生成，使用根技术页的本地合成做独立验证。

默认开发模式没有强制账号门禁，只监听本机，不要把这一模式公开到互联网。

## 2. 配置文件

复制示例到本地配置目录，真实文件被 `.gitignore` 排除：

```text
backend/config/settings.example.json
  -> backend/.local/settings.json
backend/config/model-profiles.example.json
  -> backend/.local/model-profiles.json
```

先自行创建 `backend/.local/`。`settings.json` 中填写自己的 SSH 主机、用户名、密码与已核验主机指纹，以及可用的 Gemini 模型名。Google 官方音频 API 需自己的 Key，可用环境变量 `GEMINI_API_KEY`。示例模型名是占位，不保证你的账号有访问权限。

文字翻译使用独立 profile：配置文字 API URL、模型、自己的 API Key，并设置 `SONIC_TRANSLATION_PROFILE_ID=translation`。不要把文字翻译成功当成音频模型调用成功。

`backend/.env.example` 仅是环境变量清单，主 API 不会自动加载它。通过 shell、容器或进程管理器注入变量。线上建议：

```text
SONIC_PUBLIC_MODE=1
SONIC_USER_ACCOUNTS=1
SONIC_PUBLIC_ORIGIN=https://your-own-domain.example
SONIC_APP_TOKEN=<自己的随机长令牌>
```

App 门禁不代替个人用户会话。没有账号服务时不要设置成“开放共享数据”来绕过问题。

## 3. 账号服务

要求 PostgreSQL 与独立数据库用户。不要使用开发测试命令处理真实用户库。

1. 在 `business/` 建自己的虚拟环境，安装 `requirements.lock`。
2. 复制 `.env.example` 为 `.env`，填写数据库 URL、数据库密码和至少 32 字符的随机 JWT 密钥。
3. 在 `business/` 工作目录，设置 `PYTHONPATH=src`。
4. 运行迁移与服务：

```bash
python -m alembic upgrade head
python -m uvicorn sonic.main:create_app --factory --host 127.0.0.1 --port 8790
```

Windows PowerShell 设置方式：`$env:PYTHONPATH='src'`。

主 API 经回环 `8790` 代理账号请求。数据库、账号服务与上传文件不直接对外开放。修改密码会撤销现有会话；缺少新版会话标识的旧登录需重新登录。

## 4. GPU 音乐与六轨服务

按照 [THIRD_PARTY.md](../THIRD_PARTY.md) 的官方来源准备 YuE2/VAE、Demucs 和可选 Basic Pitch。模型与代码许可需要分别核对。

主服务通过 `remote_music.py` 把运行脚本和输入发送到 GPU。配置项 `yue2_project`、`yue2_model`、`yue2_vae`、`basic_pitch_python` 可覆盖部分路径。当前仍有路径约定，例如 `/srv/sonic`、`/opt/conda/bin/python` 和任务输出目录；迁移时必须检查 `remote_music.py`、`live_bridge.py`、`live_separate_runtime.py`。这不是一键配置化的通用模型部署器。

六轨服务：进入 `gpu-audio/`，安装自己的 `requirements.txt`，设置 `SONIC_ROOT` 和 `SONIC_MVP_ROOT`。旧 `studio/` 是后端导入要求，当前编辑 UI 在 `live/`。

```bash
python -m uvicorn sonic_live:app --host 127.0.0.1 --port 8001
```

`8001` 仅监听回环。主服务负责资源权限和代理，GPU 原始服务不能单独公开。分轨的输入是生成歌曲，不能凭空从 API 返回值证明分轨听感合格。

## 5. Android 与可选硬件

需要 JDK 21、Android SDK 35、Gradle 8.11.1；应用最低 Android API 26。依赖由 Gradle 解析。

1. 按自己的授权渠道取得适配的设备 SDK。恢复 `android/vendor-repo/` 中 Gradle 声明的本地 Maven 依赖。仓库不包含 SDK、AAR、接口文档或底层协议。
2. 复制 `.private-connection.example.json` 为 `.private-connection.json`，填写自己的 HTTPS 后端和独立 App 连接凭据。
3. `android/local.properties` 指向自己的 Android SDK。
4. 在 `android/` 执行 `gradlew.bat assembleDebug`。没有授权 SDK 时构建会因缺少依赖失败，不提供假成功替代。

硬件授权/解码服务为受限外部依赖，公开快照不附实现或官方请求协议。`hardware_integration.py` 保留外围适配边界；设备能力需在自己的授权环境重新接线。普通上传和非设备音频路径可独立使用。

不要把历史赛事 APK 作为 GitHub Release；它可能仍嵌有旧的应用连接令牌和专有组件。自己的分发版应使用自己的部署配置和获准依赖。
