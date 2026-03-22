# MobiClaw Android 客户端

MobiClaw Android 客户端可基于 Tailscale 网络，将 Android 设备注册到 MobiClaw Gateway Server，并自动建立和维护 ADB 无线调试连接，让 MobiClaw 的 Agent 能够操作手机。

## 功能特性

- 🔄 自动发现 ADB 无线调试端口(通过 mDNS)
- 🌐 通过 Tailscale 网络与 Gateway Server 通信
- 🔌 自动建立并维护 ADB 连接
- 💓 后台运行，定期发送心跳保持设备在线状态

## 编译流程

### 1. 环境准备

确保已安装以下工具:

```bash
# 检查 Java 版本 (需要 JDK 21)
java -version

# 检查 Android SDK
echo $ANDROID_HOME
```

### 2. 编译 APK

```bash
git clone https://github.com/IPADS-SAI/MobiClaw.git
cd MobiClaw/apps/android

# Debug 版本
./gradlew assembleDebug

# Release 版本 (需要签名配置)
./gradlew assembleRelease
```

编译产物位置:
- Debug: `app/build/outputs/apk/debug/app-debug.apk`
- Release: `app/build/outputs/apk/release/app-release.apk`

### 3. 安装到设备

```bash
adb install app/build/outputs/apk/debug/app-debug.apk
```

## 使用方法

### 前置准备

#### 1. 安装并配置 Tailscale

在 Android 设备和 Gateway Server 所在机器上都需要安装 Tailscale:

**Android 设备:**
1. 下载安装 [Tailscale](https://tailscale.com/download/android)
2. 登录 Tailscale 账号
3. 确保设备已连接到 Tailscale 网络

**Gateway Server:**
```bash
# 安装 Tailscale (以 Ubuntu/Debian 为例)
curl -fsSL https://tailscale.com/install.sh | sh

# 启动并登录
sudo tailscale up

# 查看 Tailscale IP
tailscale ip -4
```

#### 2. 启用 ADB 无线调试

在 Android 设备上:

1. 进入 `设置` → `关于手机`
2. 连续点击 `版本号` 7 次,启用开发者选项
3. 返回 `设置` → `系统` → `开发者选项`
4. 启用 `无线调试` (Wireless debugging)

#### 3. 启动 Gateway Server

在 Gateway Server 机器上:

```bash
python -m mobiclaw.gateway_server
```

具体安装流程见[MobiClaw 主项目README](../../README.md)。该方式下不需要手动设置 `MOBILE_DEVICE_ID` 环境变量，Gateway Server会自动查找并连接已经注册的设备。

### 连接流程

#### 1. 配置 Gateway Server 连接信息

在应用主界面填写以下信息:

- **Gateway Host**: Gateway Server 的 Tailscale IP 或域名
  - 示例: `100.101.102.103` (Gateway Server 的 Tailscale IP)
- **Gateway Port**: Gateway Server 监听端口
  - 默认: `8090`
- **API Key**: Gateway Server 的 API 密钥 (可选)
  - 如果 Gateway Server 设置了 `MOBICLAW_GATEWAY_API_KEY`,则必须填写
  - 如果未设置,可留空

#### 2. 检查设备状态

应用会自动检测以下信息:

- **Detected ADB Port**: 自动发现的 ADB 无线调试端口
  - 如果显示 "Not detected",请检查无线调试是否已启用
  - 如果确认无线调试已经启用，仍无法检测到，可以手动输入设置中显示的 ADB 调试端口
- **Detected Tailscale IP**: 自动检测的 Tailscale IP
  - 如果显示 "Not detected",请检查 Tailscale 是否已连接

#### 3. 连接到Gateway Server

点击 Connect 按钮后，应用会通过后台发送心跳包，与 Gateway Server 保持连接，此时手机会显示"已连接到无线调试"。接下来，你可以通过飞书、命令行等方式向 MobiClaw 发送指令，MobiClaw 会控制你的手机完成指定任务。

### 4. 断开连接

点击 Disconnect 按钮后，应用会向 Gateway Server 发送注销请求，之前建立的ADB连接将断开。


