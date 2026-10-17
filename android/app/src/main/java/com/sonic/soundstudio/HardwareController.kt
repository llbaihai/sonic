package com.sonic.soundstudio

import android.app.Activity
import android.content.pm.PackageManager
import android.os.Build
import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.sonic.hardware.*
import com.google.gson.Gson
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.UUID
import java.util.concurrent.Executors

/** Android owns SDK credentials and Bluetooth. Web content only sees public state. */
class HardwareController(private val activity:Activity,private val web:WebView) {
 private var bridge=SoundcoreBridge(activity.application)
 @Volatile private var accountGeneration=0
 private val executor=Executors.newSingleThreadExecutor()
 private var pendingScan=false
 private var pendingImport:Int?=null
 private var closed=false
 private var uploading=false
 private val preferences=activity.getSharedPreferences("soundcore",0)
 private val deviceId=preferences.getString("deviceId",null)?:UUID.randomUUID().toString().also{preferences.edit().putString("deviceId",it).apply()}
 init { observeBridge() }
 private fun observeBridge(){ bridge.onState={state->publish();if(pendingScan&&state.authReady){pendingScan=false;bridge.scan()};val record=state.recordings.firstOrNull{it.id==pendingImport&&it.localPath!=null};if(record!=null&&!uploading){pendingImport=null;upload(record)}} }
 private fun emit(name:String,payload:String){activity.runOnUiThread{if(!closed)web.evaluateJavascript("window.dispatchEvent(new CustomEvent("+JSONObject.quote(name)+",{detail:"+payload+"}))",null)}}
 private fun message(text:String)=emit("sonic-hardware-message",JSONObject().put("message",text).toString())
 private fun publish(){val json=JSONObject(Gson().toJson(bridge.state));json.optJSONArray("recordings")?.let{for(i in 0 until it.length())it.getJSONObject(i).remove("localPath")};json.put("uploading",uploading);emit("sonic-hardware-state",json.toString())}
 @JavascriptInterface fun action(action:String,value:String){activity.runOnUiThread{
  try{when(action){
   "resetAccount"->{accountGeneration++;pendingScan=false;pendingImport=null;bridge.onState=null;bridge.shutdown();bridge=SoundcoreBridge(activity.application);observeBridge();publish()}
   "state"->publish()
   "scan"->{pendingScan=true;permissionsResult()}
   "stopScan"->bridge.stopScan()
   "connect"->bridge.connect(value)
   "disconnect"->bridge.disconnect()
   "bind"->bridge.bind()
   "refresh"->{bridge.refreshInfo();bridge.refreshFiles()}
   "import"->{val id=value.toIntOrNull()?:return@runOnUiThread;val record=bridge.state.recordings.firstOrNull{it.id==id}?:return@runOnUiThread;if(record.localPath!=null)upload(record)else{pendingImport=id;bridge.syncFile(id)}}
  }}catch(error:Throwable){message("设备操作未完成，请重新连接；"+error.javaClass.simpleName)}
 }}
 fun permissionsResult(){
  val missing=requiredScanPermissions(Build.VERSION.SDK_INT).filter{activity.checkSelfPermission(it)!=PackageManager.PERMISSION_GRANTED}
  if(missing.isNotEmpty()){pendingScan=true;activity.requestPermissions(missing.toTypedArray(),101);return}
  if(bridge.state.authReady){pendingScan=false;bridge.scan();return}
  message("正在获取录音豆授权…")
  val generation=accountGeneration
  val requestUser=NativeHttp.userToken
  executor.execute{try{
   if(generation!=accountGeneration)return@execute
   val response=NativeHttp.requestAs(BuildConfig.BACKEND_URL,BuildConfig.APP_ACCESS_TOKEN,"POST","/api/device/credentials",JSONObject().put("deviceId",deviceId).toString().toByteArray(),"application/json",requestUser)
   if(response.status!=200)throw IllegalStateException("授权服务 "+response.status)
   val json=JSONObject(String(response.body,Charsets.UTF_8))
   val credentials=SdkCredentials(json.getString("userId"),json.getString("deviceId"),json.getString("token"),json.get("licenseJson").toString(),json.getString("licenseSignature"),json.optString("environment")=="PRODUCTION")
   activity.runOnUiThread{if(generation!=accountGeneration)return@runOnUiThread;try{bridge.initialize(credentials)}catch(error:Throwable){message("SDK初始化失败："+error.javaClass.simpleName)}}
  }catch(error:Exception){pendingScan=false;message("录音豆授权暂未完成，请稍后重试。")}}
 }
 fun permissionDenied(){pendingScan=false;message("需要附近设备和位置权限才能搜索录音豆，请在系统设置允许后重试。");}
 private fun upload(record:HardwareRecording){
  if(uploading)return
  val file=File(record.localPath?:return);if(!file.isFile||file.length()>12*1024*1024){message("录音文件不可用或超过12MB，请选择较短录音。");return}
  val generation=accountGeneration
  val requestUser=NativeHttp.userToken
  uploading=true;publish();message("正在同步录音和重点标记…")
  executor.execute{try{
   if(generation!=accountGeneration)return@execute
   val boundary="Sonic-"+UUID.randomUUID();val output=ByteArrayOutputStream()
   fun text(value:String){output.write(value.toByteArray(Charsets.UTF_8))}
   for((key,value) in mapOf("deviceFileId" to record.id.toString(),"durationMs" to record.durationMs.toString(),"markersMs" to record.markersMs.joinToString(",")))text("--$boundary\r\nContent-Disposition: form-data; name=\"$key\"\r\n\r\n$value\r\n")
   text("--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"recording.opus\"\r\nContent-Type: application/octet-stream\r\n\r\n");file.inputStream().use{it.copyTo(output)};text("\r\n--$boundary--\r\n")
   if(generation!=accountGeneration)return@execute
   val result=NativeHttp.requestAs(BuildConfig.BACKEND_URL,BuildConfig.APP_ACCESS_TOKEN,"POST","/api/device/import",output.toByteArray(),"multipart/form-data; boundary=$boundary",requestUser)
   if(result.status!=200)throw IllegalStateException("同步服务 "+result.status)
   if(generation!=accountGeneration)return@execute
   emit("sonic-hardware-imported",String(result.body,Charsets.UTF_8));message("录音已加入我的声音，可以开始创作。")
  }catch(error:Exception){message("同步未完成，请重试。原始录音仍保留在设备中。")}
   finally{uploading=false;publish()}
  }
 }
 fun close(){closed=true;bridge.shutdown();executor.shutdownNow()}
}
