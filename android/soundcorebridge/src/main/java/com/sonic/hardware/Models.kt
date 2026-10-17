package com.sonic.hardware

/** All classes in this file are Sonic-owned API, not vendor SDK DTOs. */
data class SdkCredentials(
    val userId: String,
    val deviceId: String,
    val token: String,
    val licenseJson: String,
    val licenseSignature: String,
    val production: Boolean = true
)

data class RecorderDevice(
    val id: String,
    val name: String,
    val macAddress: String,
    val uuid: String,
    val rssi: Int?,
    val bindingStatus: String?
)

data class RecorderInfo(
    val batteryPercent: Int? = null,
    val firmware: String = "—",
    val freeMemoryKb: Int? = null,
    val totalMemoryKb: Int? = null,
    val recording: Boolean? = null
)

data class HardwareRecording(
    val id: Int,
    val durationMs: Int,
    val sizeBytes: Int,
    val fileStateCode: Int,
    val localPath: String? = null,
    val markersMs: List<Int> = emptyList(),
    val transferPercent: Int = 0
)

data class BridgeState(
    val auth: String = "未鉴权",
    val authReady: Boolean = false,
    val scanning: Boolean = false,
    val devices: List<RecorderDevice> = emptyList(),
    val connected: RecorderDevice? = null,
    val connectingId: String? = null,
    val info: RecorderInfo? = null,
    val recordingStatus: String = "未知",
    val recordingDurationMs: Long = 0L,
    val recordings: List<HardwareRecording> = emptyList(),
    val loadingFiles: Boolean = false,
    val transferId: Int? = null,
    val transferPhase: String = "",
    val sppConnected: Boolean = false,
    val timeSynced: Boolean? = null,
    val message: String = "请配置 Sonic 后端并初始化设备 SDK",
    val error: String? = null
)
