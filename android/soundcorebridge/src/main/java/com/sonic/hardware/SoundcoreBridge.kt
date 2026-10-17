package com.sonic.hardware

import android.app.Application
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.oceanwing.soundcore.spplink.SoundcoreSDK
import com.oceanwing.soundcore.spplink.listener.*
import com.oceanwing.soundcore.spplink.log.LogInterface
import com.oceanwing.soundcore.spplink.model.BlueDeviceModel
import com.oceanwing.soundcore.spplink.model.SDKInitConfig
import com.oceanwing.soundcore.spplink.model.SdkException
import com.oceanwing.soundcore.spplink.service.audio.model.FileInfoModel
import com.oceanwing.soundcore.spplink.service.audio.model.OfflineFileData
import com.oceanwing.soundcore.spplink.service.auth.*
import com.oceanwing.soundcore.spplink.service.deviceinfo.DeviceInfo
import com.oceanwing.soundcore.spplink.service.search.OnReceiveScanResult
import java.io.File

/** Single device session, immutable published state, all SDK callbacks marshalled to main thread. */
class SoundcoreBridge(private val application: Application) {
    private val sdk = SoundcoreSDK.proxy
    private val handler = Handler(Looper.getMainLooper())
    private val discovered = linkedMapOf<String, RecorderDevice>()
    private val fileModels = linkedMapOf<Int, FileInfoModel>()
    private val receivedFilePaths = linkedMapOf<Int, String>()
    private val markers = linkedMapOf<Int, List<Int>>()
    private var listening = false
    private var closed = false
    private var session: RecorderDevice? = null
    var state = BridgeState()
        private set
    var onState: ((BridgeState) -> Unit)? = null
        set(value) { field = value; value?.invoke(state) }

    private fun main(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) { if (!closed) block() }
        else handler.post { if (!closed) block() }
    }
    private fun update(block: (BridgeState) -> BridgeState) {
        main { state = block(state); onState?.invoke(state) }
    }
    private fun guard(mac: String, uuid: String): Boolean =
        session?.macAddress == mac && session?.uuid == uuid

    private val logger = object : LogInterface {
        // Never leak SDK token, license, user metadata or raw packets to logcat.
        override fun v(tag: String?, message: String?) = Unit
        override fun d(tag: String?, message: String?) = Unit
        override fun i(tag: String?, message: String?) = Unit
        override fun w(tag: String?, message: String?) = Unit
        override fun e(tag: String?, message: String?) { Log.e("SonicBridge", "SDK reported an error") }
    }
    private val businessCallback = object : BusinessAdapter() {
        override fun onGetDeviceInfo(macAddress: String, uuid: String, info: DeviceInfo) {
            main {
                if (guard(macAddress, uuid)) update { it.copy(info = RecorderInfo(
                    batteryPercent = (info.deviceBattery * 10).coerceIn(0, 100),
                    firmware = info.firmwareVersion ?: "—",
                    freeMemoryKb = info.freeMemoryKB,
                    totalMemoryKb = info.totalMemoryKB,
                    recording = info.recording?.let { v -> v != 0 }
                ), message = "设备信息已更新") }
            }
        }
        override fun onGetDeviceInfoError(macAddress: String, uuid: String, errorCode: Int) {
            main { if (guard(macAddress, uuid)) update { it.copy(error = "设备信息读取失败：$errorCode") } }
        }
        override fun onSyncTimeResult(macAddress: String, uuid: String, success: Boolean) {
            main { if (guard(macAddress, uuid)) update { it.copy(timeSynced = success,
                message = if (success) "设备时间同步成功" else "设备时间同步失败，请重试连接") } }
        }
        override fun onBatteryInfoChanged(macAddress: String, uuid: String, battery: Int, chargingStatus: Int) {
            main { if (guard(macAddress, uuid)) update { it.copy(info = (it.info ?: RecorderInfo()).copy(batteryPercent = (battery * 10).coerceIn(0, 100))) } }
        }
        override fun onAudioRecordStatusChanged(macAddress: String, uuid: String, status: Int, recordingDuration: Int, fileID: Int) {
            main { if (guard(macAddress, uuid)) update { it.copy(
                recordingStatus = when(status) { SoundcoreBusinessCallback.RECORDING -> "录音中";
                    SoundcoreBusinessCallback.PAUSE_RECORD -> "已暂停"; else -> "未录音" },
                recordingDurationMs = recordingDuration.toLong()
            ) } }
        }
        override fun onAudioRecordDuration(macAddress: String, uuid: String, durationMs: Long) {
            main { if (guard(macAddress, uuid)) update { it.copy(recordingDurationMs = durationMs) } }
        }
        override fun onReceiveOfflineFile(macAddress: String, uuid: String, offlineFileData: OfflineFileData, isAllAudioFiles: Boolean) {
            main {
                if (!guard(macAddress, uuid)) return@main
                offlineFileData.fileInfoList.forEach { fileModels[it.time] = it }
                val list = fileModels.values.sortedByDescending { it.time }.map { model ->
                    val old = state.recordings.firstOrNull { it.id == model.time }
                    HardwareRecording(model.time, model.duration, model.size, model.code,
                        receivedFilePaths[model.time] ?: old?.localPath,
                        markers[model.time] ?: old?.markersMs ?: emptyList(), old?.transferPercent ?: 0)
                }
                update { it.copy(loadingFiles = false, recordings = list,
                    message = "设备返回 ${offlineFileData.fileCount} 条录音（当前显示 ${list.size} 条）",
                    error = null) }
            }
        }
        override fun onReceiveOfflineFileError(macAddress: String, uuid: String, errorCode: Int) {
            main { if (guard(macAddress, uuid)) update { it.copy(loadingFiles = false,
                error = "读取录音列表失败 $errorCode；请稍后重试") } }
        }
        override fun onTransferFileStatusChanged(macAddress: String, uuid: String, fileID: Int, status: Int, markTimeStampList: ArrayList<Int>) {
            main {
                if (!guard(macAddress, uuid)) return@main
                if (markTimeStampList.isNotEmpty()) markers[fileID] = markTimeStampList.toList()
                val phase = when(status) {
                    SoundcoreBusinessCallback.WAIT_TRANSFER -> "等待传输"
                    SoundcoreBusinessCallback.TRANSFERRING -> "传输中"
                    SoundcoreBusinessCallback.DECODING -> "SDK 处理中"
                    SoundcoreBusinessCallback.FILE_TRANSFER_COMPLETED -> "传输完成，等待文件回调"
                    SoundcoreBusinessCallback.REAL_TIME_TRANSFER_COMPLETED -> "实时传输结束"
                    else -> "处理中 $status"
                }
                update { it.copy(transferPhase = phase, recordings = it.recordings.map { r ->
                    if (r.id == fileID) r.copy(markersMs = markers[fileID] ?: r.markersMs) else r }) }
            }
        }
        override fun onTransferFileProgress(macAddress: String, uuid: String, fileID: Int, total: Int, current: Int, sequenceNumber: Int, isMark: Boolean) {
            main {
                if (!guard(macAddress, uuid)) return@main
                val pct = if (total > 0) (current.toLong() * 100L / total).toInt().coerceIn(0, 100) else 0
                update { it.copy(transferId = fileID, transferPhase = "蓝牙传输 $pct%",
                    recordings = it.recordings.map { r -> if (r.id == fileID) r.copy(transferPercent = pct) else r }) }
            }
        }
        override fun onTransferFileSupplementProgress(macAddress: String, uuid: String, fileID: Int, total: Int, current: Int) {
            main { if (guard(macAddress, uuid)) update { it.copy(transferPhase = "校验补包 $current/$total") } }
        }
        override fun onDecodeFileMarkers(macAddress: String, uuid: String, fileID: Int, markerTimes: IntArray) {
            main {
                if (!guard(macAddress, uuid)) return@main
                markers[fileID] = markerTimes.toList()
                update { it.copy(recordings = it.recordings.map { r ->
                    if (r.id == fileID) r.copy(markersMs = markerTimes.toList()) else r }) }
            }
        }
        override fun onDecodeFileCompleted(macAddress: String, uuid: String, fileID: Int, filePath: String) {
            main {
                if (!guard(macAddress, uuid)) return@main
                val file = File(filePath)
                if (!file.isFile || file.length() == 0L) {
                    update { it.copy(transferId = null, transferPhase = "", error = "SDK 完成回调，但输出文件不存在或为空") }
                    return@main
                }
                receivedFilePaths[fileID] = file.absolutePath
                update { it.copy(transferId = null, transferPhase = "",
                    recordings = it.recordings.map { r -> if (r.id == fileID)
                        r.copy(localPath = file.absolutePath, markersMs = markers[fileID] ?: r.markersMs,
                            transferPercent = 100) else r },
                    message = "录音 $fileID 已同步，可上传 Sonic 进行解码和 AI 分析", error = null) }
            }
        }
        override fun onTransferFileError(macAddress: String, uuid: String, fileID: Int, errorType: FileTransferError) {
            main { if (guard(macAddress, uuid)) update { it.copy(transferId = null, transferPhase = "",
                error = "文件 $fileID 同步失败：${errorType.name}，可重试") } }
        }
        override fun onTransferFileTransmitting(macAddress: String, uuid: String, fileID: Int) {
            main { if (guard(macAddress, uuid)) update { it.copy(error = "设备正在同步另一段录音，请稍等") } }
        }
        override fun onConnectSPPLinkStatusChanged(macAddress: String, uuid: String, isConnect: Boolean) {
            main { if (guard(macAddress, uuid)) update { it.copy(sppConnected = isConnect) } }
        }
        override fun onSendBindingDeviceCMDSuccess(macAddress: String, uuid: String) {
            main { if (guard(macAddress, uuid)) update { it.copy(message = "绑定请求已发出，请在充电盒确认") } }
        }
        override fun onDeviceBindConfirm(macAddress: String, uuid: String) {
            main { if (guard(macAddress, uuid)) update { it.copy(message = "设备绑定成功") } }
        }
        override fun onBindingDeviceError(macAddress: String, uuid: String, errorCode: Int) {
            main { if (guard(macAddress, uuid)) update { it.copy(error = "绑定失败：$errorCode") } }
        }
    }

    fun initialize(credentials: SdkCredentials) {
        if (credentials.userId.isBlank() || credentials.deviceId.isBlank() ||
            credentials.token.isBlank() || credentials.licenseJson.isBlank() ||
            credentials.licenseSignature.isBlank()) {
            update { it.copy(auth = "凭证不完整", authReady = false, error = "SDK 凭证缺失，请检查后端") }
            return
        }
        update { it.copy(auth = "鉴权中…", authReady = false, error = null) }
        try {
            val config = SDKInitConfig(
                context = application, userId = credentials.userId, deviceId = credentials.deviceId,
                token = credentials.token, licenseJson = credentials.licenseJson,
                license_signature = credentials.licenseSignature,
                environment = if (credentials.production) SDKEnvironment.PRODUCTION else SDKEnvironment.DEVELOPMENT,
                isDebug = false,
                firmwareLogFilePath = File(application.filesDir, "firmware_log").apply { mkdirs() }.absolutePath,
                commandTimeOut = 5L,
                fileTransferInterval = 200L
            )
            sdk.initSDK(logger, config, object : OnAuthResultListener {
                override fun onAuthResult(result: AuthResult) {
                    main {
                        if (result.success) {
                            if (!listening) { sdk.registerBusinessCallback(businessCallback); listening = true }
                            update { it.copy(auth = "已授权", authReady = true, error = null, message = "SDK 已就绪，开始寻找录音豆") }
                        } else update { it.copy(auth = "鉴权失败", authReady = false,
                            error = "厂商 SDK 鉴权失败：${result.error?.message ?: "未知原因"}") }
                    }
                }
            })
        } catch (e: LinkageError) {
            update { it.copy(auth = "SDK 依赖不兼容", authReady = false,
                error = "SDK 初始化依赖错误：${e.javaClass.simpleName}。请安装修复版应用。") }
            throw e // Activity records a credential-free diagnostic and reports it safely.
        } catch (e: Exception) {
            update { it.copy(auth = "初始化异常", authReady = false, error = "SDK 无法初始化：${e.javaClass.simpleName}") }
        }
    }

    fun scan() {
        if (!state.authReady) { update { it.copy(error = "请先初始化 SDK") }; return }
        stopScan()
        discovered.clear()
        update { it.copy(devices = emptyList(), scanning = true, error = null, message = "正在搜索 Soundcore 设备…") }
        try {
            sdk.startScan(true, true, arrayListOf(), object : OnReceiveScanResult {
                override fun onScanSuccess(device: BlueDeviceModel) {
                    val mac = device.macAddress ?: return
                    val uuid = device.uuid ?: return
                    main {
                        val d = RecorderDevice("$mac|$uuid", device.deviceName ?: "Soundcore Work",
                            mac, uuid, device.rssi, device.bindingStatus)
                        discovered[d.id] = d
                        update { it.copy(devices = discovered.values.toList(), message = "找到 ${discovered.size} 台设备") }
                    }
                }
                override fun onScanError(result: Int) {
                    main {
                        if (result == 1 || result == 2) update { it.copy(scanning = false,
                            message = if (result == 1) "扫描已结束" else "扫描已停止") }
                        else update { it.copy(error = "扫描状态：$result") }
                    }
                }
            })
        } catch (e: Exception) { update { it.copy(scanning = false, error = "扫描启动失败：${e.javaClass.simpleName}") } }
    }
    fun stopScan() {
        try { sdk.stopScan() } catch (_: Exception) { }
        update { it.copy(scanning = false) }
    }
    fun connect(id: String) {
        if (!state.authReady) { update { it.copy(error = "请先完成 SDK 鉴权") }; return }
        val device = discovered[id] ?: return
        stopScan()
        session = device
        update { it.copy(connectingId = id, connected = null, timeSynced = null,
            error = null, message = "连接 ${device.name}…") }
        try {
            sdk.connect(device.macAddress, device.uuid, object : SoundcoreConnectionCallback {
                override fun onConnected(macAddress: String?, deviceName: String?) {
                    main {
                        if (session?.macAddress != macAddress) return@main
                        update { it.copy(connected = device, connectingId = null,
                            message = "已连接，正在同步设备时间", error = null) }
                        try { sdk.syncTime(device.macAddress, device.uuid, (System.currentTimeMillis() / 1000).toUInt()) }
                        catch (e: Exception) { update { it.copy(error = "时间同步命令失败：${e.javaClass.simpleName}") } }
                        refreshInfo()
                        refreshFiles()
                    }
                }
                override fun onDisconnected(macAddress: String?, systemCode: Int?, systemMsg: String?) {
                    main {
                        if (session?.macAddress != macAddress) return@main
                        session = null
                        update { it.copy(connected = null, connectingId = null, transferId = null,
                            transferPhase = "", info = null,
                            message = "设备已断开，可重新扫描连接",
                            error = if (systemCode != null) "蓝牙断开（$systemCode）" else null) }
                    }
                }
                override fun onError(macAddress: String?, exception: SdkException) {
                    main {
                        if (session?.macAddress != macAddress) return@main
                        session = null
                        update { it.copy(connectingId = null, connected = null,
                            error = "连接失败 ${exception.code}：${exception.message}") }
                    }
                }
            })
        } catch (e: Exception) { session = null; update { it.copy(connectingId = null, error = "连接异常：${e.javaClass.simpleName}") } }
    }
    fun disconnect() {
        val s = session ?: return
        try { sdk.disconnect(s.macAddress, s.uuid) }
        catch (e: Exception) { update { it.copy(error = "断开连接异常：${e.javaClass.simpleName}") } }
    }
    fun refreshInfo() {
        val s = state.connected ?: return
        try { sdk.getDeviceInfo(s.macAddress, s.uuid) }
        catch (e: Exception) { update { it.copy(error = "读取状态异常：${e.javaClass.simpleName}") } }
    }
    fun refreshFiles() {
        val s = state.connected ?: return
        update { it.copy(loadingFiles = true, error = null, message = "读取设备录音列表…") }
        try { sdk.getAllAudioRecordFiles(s.macAddress, s.uuid) }
        catch (e: Exception) { update { it.copy(loadingFiles = false, error = "获取录音失败：${e.javaClass.simpleName}") } }
    }
    fun bind() {
        val s = state.connected ?: return
        try { sdk.bindingDevice(s.macAddress, s.uuid, true) }
        catch (e: Exception) { update { it.copy(error = "绑定请求失败：${e.javaClass.simpleName}") } }
    }
    fun syncFile(fileID: Int) {
        val s = state.connected ?: return
        if (state.transferId != null) { update { it.copy(error = "已有录音正在同步，请稍等") }; return }
        val file = fileModels[fileID] ?: run { update { it.copy(error = "录音已变化，请重新读取列表") }; return }
        val dir = File(application.filesDir, "recordings").apply { mkdirs() }
        update { it.copy(transferId = fileID, transferPhase = "请求同步…", error = null) }
        try {
            // 10 seconds threshold -> vendor SDK auto-promotes longer files to Android SPP.
            sdk.startSyncAudioFile(s.macAddress, s.uuid, arrayListOf(file), dir.absolutePath, 10_000)
        } catch (e: Exception) { update { it.copy(transferId = null, transferPhase = "", error = "同步失败：${e.javaClass.simpleName}") } }
    }
    fun shutdown() {
        val s = session
        try { sdk.stopScan() } catch (_: Exception) { }
        if (s != null) try { sdk.disconnect(s.macAddress, s.uuid) } catch (_: Exception) { }
        closed = true
        onState = null
        // sdk.close() deliberately omitted here; callers may hand this singleton to Sonic's process-scoped owner.
    }
}
