package com.sonic.hardware

import com.oceanwing.soundcore.spplink.listener.*
import com.oceanwing.soundcore.spplink.service.audio.model.OfflineFileData
import com.oceanwing.soundcore.spplink.service.deviceinfo.DeviceInfo
import com.oceanwing.soundcore.spplink.service.devicelog.DeviceLogFileInfo
import com.oceanwing.soundcore.spplink.service.wifi.WifiState

/** v1.3.02 provides no default implementations, so adapt every callback explicitly. */
open class BusinessAdapter : SoundcoreBusinessCallback {
    override fun onAudioRecordStatusChanged(macAddress: String, uuid: String, status: Int, recordingDuration: Int, fileID: Int) = Unit
    override fun onReceiveOfflineFile(macAddress: String, uuid: String, offlineFileData: OfflineFileData, isAllAudioFiles: Boolean) = Unit
    override fun onReceiveOfflineFileError(macAddress: String, uuid: String, errorCode: Int) = Unit
    override fun onAudioRecordDuration(macAddress: String, uuid: String, durationMs: Long) = Unit
    override fun onTransferFileStatusChanged(macAddress: String, uuid: String, fileID: Int, status: Int, markTimeStampList: ArrayList<Int>) = Unit
    override fun onDecodeFileCompleted(macAddress: String, uuid: String, fileID: Int, filePath: String) = Unit
    override fun onDecodeFileMarkers(macAddress: String, uuid: String, fileID: Int, markerTimes: IntArray) = Unit
    override fun onTransferFileError(macAddress: String, uuid: String, fileID: Int, errorType: FileTransferError) = Unit
    override fun onDeviceTransferStateChanged(macAddress: String, uuid: String, state: DeviceTransferState) = Unit
    override fun onOtaError(macAddress: String, uuid: String, errorCode: Int) = Unit
    override fun onTransferFileSupplementProgress(macAddress: String, uuid: String, fileID: Int, total: Int, current: Int) = Unit
    override fun onTransferFileProgress(macAddress: String, uuid: String, fileID: Int, total: Int, current: Int, sequenceNumber: Int, isMark: Boolean) = Unit
    override fun onReceiveAudioFragment(macAddress: String, uuid: String, fileID: Int, audioData: ByteArray, isAppendPreAudio: Boolean, sequenceNumber: Int, isMark: Boolean) = Unit
    override fun onGetDeviceInfo(macAddress: String, uuid: String, info: DeviceInfo) = Unit
    override fun onGetDeviceInfoError(macAddress: String, uuid: String, errorCode: Int) = Unit
    override fun onDeleteFileResult(macAddress: String, uuid: String, fileID: Int, isSuccess: Boolean) = Unit
    override fun onSendBindingDeviceCMDSuccess(macAddress: String, uuid: String) = Unit
    override fun onSendUnbindingDeviceCMDSuccess(macAddress: String, uuid: String) = Unit
    override fun onDeviceBindConfirm(macAddress: String, uuid: String) = Unit
    override fun onDeviceLogEnableCollectionResult(macAddress: String, uuid: String, success: Boolean) = Unit
    override fun onDeviceLogRequestListResult(macAddress: String, uuid: String, success: Boolean, fileCount: Int, fileList: List<DeviceLogFileInfo>, logCollectionStatus: Boolean) = Unit
    override fun onDeviceLogTransferResult(macAddress: String, uuid: String, success: Boolean, code: Int) = Unit
    override fun onDeviceLogDeleteResult(macAddress: String, uuid: String, success: Boolean) = Unit
    override fun onDeviceLogDumping(macAddress: String, uuid: String, success: Boolean, packetSequence: Int, fileTimestamp: Int, transferStatus: Int, progress: Int, fragmentData: ByteArray) = Unit
    override fun onBindingDeviceError(macAddress: String, uuid: String, errorCode: Int) = Unit
    override fun onSyncTimeResult(macAddress: String, uuid: String, success: Boolean) = Unit
    override fun onDeviceChargingStatusChanged(macAddress: String, uuid: String, chargingStatus: Int, battery: Int) = Unit
    override fun onBatteryInfoChanged(macAddress: String, uuid: String, battery: Int, chargingStatus: Int) = Unit
    override fun onResetDeviceResult(macAddress: String, uuid: String, success: Boolean) = Unit
    override fun onFindMyEnableResult(macAddress: String, uuid: String, success: Boolean) = Unit
    override fun onReceiveWaitingDeviceOpenWIFI(macAddress: String, uuid: String) = Unit
    override fun onWifiScanned(macAddress: String, uuid: String) = Unit
    override fun onConnectWIFIError(macAddress: String, uuid: String, errorCode: Int) = Unit
    override fun onConnectWIFIStatusChanged(macAddress: String, uuid: String, state: WifiState) = Unit
    override fun onCloseSyncFileByWIFI(macAddress: String, uuid: String) = Unit
    override fun onRealtimeTransferSwitch(macAddress: String, uuid: String, fileID: Int, filePath: String?) = Unit
    override fun onTransferFileTransmitting(macAddress: String, uuid: String, fileID: Int) = Unit
    override fun onConnectSPPLinkStatusChanged(macAddress: String, uuid: String, isConnect: Boolean) = Unit
}
