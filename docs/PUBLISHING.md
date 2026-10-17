# 推送到个人 GitHub

仓库名称建议为 `sonic`，简介可用：

> Android audio-to-music creation: editable prompts, optional humming reference, cloud generation, six-track editing and per-user access control.

GitHub Topics 建议：`android`、`fastapi`、`audio`、`music-generation`、`web-audio`、`demucs`、`postgresql`。

## 已准备

- 独立目录，不包含原比赛资料、私人联系方式、部署账号或旧 APK。
- README 包含产品、截图、演示、架构、运行方式与能力限制。
- 保留团队贡献及第三方来源，没有代替其他贡献者作整体 MIT/Apache 授权。
- 主服务 CI 配置及测试命令；数据库/GPU/设备测试明确区分。

## 创建远端后

从这个目录推送到你自己的空仓库：

```bash
git remote add origin https://github.com/YOUR_USERNAME/sonic.git
git push -u origin main
```

不要把父目录直接 `git add .` 后上传，父目录是含真实配置与赛事原始材料的工作区。不要公开旧 APK、厂商 SDK、内部授权服务或资料包。

展示可以归入个人作品集，但团队开发背景和贡献说明应保留。公开许可与第三方依赖权利分别核对，不把不随包的 SDK 或权重声称为项目原创。
