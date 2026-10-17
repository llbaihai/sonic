# 测试与验证范围

## 主服务

在根目录运行：

```bash
python -m pytest backend/tests -c pytest.ini
python scripts/check_public_repo.py
```

这组测试不需要真实设备、模型额度或 GPU。覆盖内容包括音频解码、音符/ABC、生成结果包装、模型接口校验、不同 JSON 请求类型的归属检查、媒体和导出访问、重复导入等。模型调用边界使用显式测试夹具，不冒充实际生成或真机结果。

命令显式选择 `backend/tests` 并使用根配置，避免意外收集需要数据库的业务测试。

## 账号数据库

在 `business/` 用独立 PostgreSQL 测试库运行：

```bash
# 环境变量指向自己的专用数据库，名称须以 _test 结尾
SONIC_TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost/sonic_test
PYTHONPATH=src python -m pytest -q
```

Windows 请分别设置 `$env:SONIC_TEST_DATABASE_URL`、`$env:PYTHONPATH`。测试夹具会清理专用测试库，不能用于生产数据库。验证登录、归属、会话撤销、密码修改和文件持久化需要真实数据库，不在普通 Python 主服务 CI 中伪装通过。

## 设备、GPU 和音质

需要分别验证：授权、SDK 初始化、蓝牙连接、录音与标记、下载、云端导入；YuE2 正常输出、旋律相似度、实际时长；Demucs 串音及编辑/导出听感。接口成功不覆盖这些验收。

项目整理前保留的记录为主服务 100 项与 16 个子测试、账号数据库 30 项，以及不同日期的浏览器/JVM/公网接口实测。它们属于集成开发记录；本次候选仓库的实际新结果记录在 `docs/VALIDATION.md`，不将旧记录自动写成 GitHub Actions 已通过。
