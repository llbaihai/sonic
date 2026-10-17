# 第三方依赖与发布范围

## 不随仓库发布

- 安克专有 SDK 源码、AAR、通信协议、内部集成文档及授权资源。
- 官方示例工程、厂商密钥、许可证正文及测试账号。
- 赛事 APK：旧安装包包含原型连接凭据且依赖授权组件，不作为公开 Release。
- 云端凭据、数据库、原始录音、运行日志、备份、模型权重及第三方完整仓库。

`android/soundcorebridge` 只保留团队编写的外围模型、调用适配与回调处理。它不是 SDK 实现，不能替代厂商授权。没有授权依赖时不承诺硬件模块可构建或使用。内部硬件授权/解码服务不附公开实现。

## 外部项目

| 依赖 | 用途 | 来源 |
| --- | --- | --- |
| YuE2 / VAE | 音乐生成 | https://github.com/multimodal-art-projection/YuE |
| YuE2-3B | 生成模型发布页 | https://huggingface.co/m-a-p/YuE2-3B |
| Demucs | 六轨分离 | https://github.com/facebookresearch/demucs |
| Basic Pitch | 可选音符转录 | https://github.com/spotify/basic-pitch |
| librosa | pYIN / 音频特征 | https://github.com/librosa/librosa |
| Lucide | 界面图标，ISC | https://github.com/lucide-icons/lucide |
| Pedalboard | 局部变调 / 导出 | https://github.com/spotify/pedalboard |
| FastAPI / SQLAlchemy / PostgreSQL | API、持久化与权限 | 对应依赖官方项目 |
| MMA / FluidSynth | 历史规则编曲实验 | 对应依赖官方项目 |

模型代码、权重、云 API 和 SDK 可能具有不同的许可与服务条款；安装和使用时应分别核对，不以本仓库代为授予商业或再分发权限。

Gradle wrapper 保留原版权声明与通用构建文件，Android 依赖通过各自仓库坐标获取。图标库通过 `scripts/prepare_icons.py` 准备，不复制第三方完整源码到版本记录。

## 本仓库的许可状态

这是团队贡献代码的展示与复现候选仓库，未自动为全部协作代码和设计添加统一开源许可。没有明确许可证不等同于可任意再利用；如需采用某一开源协议，应先确认贡献者授权及与依赖的兼容性。
