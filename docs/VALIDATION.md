# 候选仓库验证

2026-10-17，本地独立候选目录验证结果：

| 项目 | 本次结果 |
| --- | --- |
| 主服务测试 | `python -m pytest backend/tests -c pytest.ini -q`：100 passed，16 subtests passed |
| 依赖警告 | 一个既有 Starlette/httpx TestClient 弃用提示，不影响本次测试通过 |
| 公开目录检查 | 检查私有/生成目录、常见凭据模式、文件大小、Python 与 JSON 语法 |
| 配置示例 | 空值或占位；没有服务器账号、密码、API Key 或用户数据 |
| 媒体 | 团队提供的五张界面图、Logo 与约 47 秒演示视频，未附旧 APK |
| 第三方边界 | 未附官方 SDK、AAR、底层协议、模型权重或完整第三方仓库 |

上述主服务测试使用模型/身份测试夹具；不代表本次重新执行了 YuE2、Gemini、真实蓝牙或数据库集成验收。PostgreSQL 测试需单独数据库，本次候选仓库整理未重跑。

GitHub Actions 配置已放入 `.github/workflows/backend.yml`，推送前没有远端 Actions 运行结果，不称为云端 CI 已通过。首次推送后仍应核对依赖安装和远端测试结果。
