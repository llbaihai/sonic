# Android 客户端

Java / Kotlin 原生壳，WebView 加载同源手机创作界面。原生层处理账号 Cookie、受限 HTTP、文件选择、下载和可选设备桥。

原型版本：`0.9-accounts`，最低 API 26，编译 SDK 35。

构建依赖和配置见 [SETUP](../docs/SETUP.md)。专有设备 SDK 未随仓库分发；仅有团队外围代码不能完成硬件模块构建。不要从历史 APK 中提取令牌或 SDK 作为依赖。

`local.properties`、`.private-connection.json`、签名文件及构建目录不提交。分发自己的构建前应核对依赖授权、网络地址、用户会话和导出权限。
