package com.sonic.soundstudio;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ActivityInfo;
import android.net.Uri;
import android.os.Bundle;
import android.graphics.Color;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.util.Base64;
import android.webkit.*;
import android.widget.*;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Development WebView client. No model credentials or SSH keys are bundled. */
public class MainActivity extends Activity {
    private WebView web;
    private ValueCallback<Uri[]> chooser;
    private PermissionRequest audioPermission;
    private byte[] pendingDownload;
    private final ExecutorService network=Executors.newFixedThreadPool(4);
    private volatile boolean destroyed=false;
    private HardwareController hardware;
    private String base=BuildConfig.BACKEND_URL;
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        FrameLayout frame=new FrameLayout(this);
        LinearLayout layout=new LinearLayout(this);layout.setOrientation(LinearLayout.VERTICAL);
        LinearLayout bar=new LinearLayout(this);
        Button home=new Button(this);home.setText("SONIC · 0.9");home.setOnClickListener(v->web.loadUrl(base+"/mobile/"));
        Button config=new Button(this);config.setText("重新连接");config.setOnClickListener(v->authenticate());
        bar.addView(home,new LinearLayout.LayoutParams(0,48,1));bar.addView(config,new LinearLayout.LayoutParams(0,48,1));
        bar.setVisibility(LinearLayout.GONE);layout.addView(bar,new LinearLayout.LayoutParams(0,0));web=new WebView(this);layout.addView(web,new LinearLayout.LayoutParams(-1,0,1));
        frame.addView(layout,new FrameLayout.LayoutParams(-1,-1));
        FrameLayout splash=new FrameLayout(this);splash.setBackgroundColor(Color.rgb(254,241,231));
        ImageView splashLogo=new ImageView(this);splashLogo.setImageResource(R.drawable.sonic_logo);splashLogo.setScaleType(ImageView.ScaleType.FIT_CENTER);
        android.util.DisplayMetrics metrics=getResources().getDisplayMetrics();
        int logoSize=(int)Math.sqrt((double)metrics.widthPixels*metrics.heightPixels/20.0);
        FrameLayout.LayoutParams logoParams=new FrameLayout.LayoutParams(logoSize,logoSize,android.view.Gravity.CENTER);
        splash.addView(splashLogo,logoParams);frame.addView(splash,new FrameLayout.LayoutParams(-1,-1));setContentView(frame);
        web.getSettings().setJavaScriptEnabled(true);web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setAllowFileAccess(false);web.getSettings().setAllowContentAccess(true);
        web.getSettings().setMediaPlaybackRequiresUserGesture(true);
        web.setWebViewClient(new WebViewClient(){
            @Override public void onPageFinished(WebView view,String url){
                setRequestedOrientation(Uri.parse(url).getPath().startsWith("/live/") ? ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE : ActivityInfo.SCREEN_ORIENTATION_PORTRAIT);
                runOnUiThread(()->{if(splash.getParent()!=null)frame.removeView(splash);});
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
                if(trusted(request.getUrl()))return false;
                Toast.makeText(MainActivity.this,"仅允许访问已配置的后端",Toast.LENGTH_SHORT).show();return true;
            }
            @Override public void onReceivedError(WebView view,WebResourceRequest req,WebResourceError err){
                if(req.isForMainFrame())Toast.makeText(MainActivity.this,"连接服务器失败，请检查网络并重新连接",Toast.LENGTH_LONG).show();
            }
        });
        web.setWebChromeClient(new WebChromeClient(){
            @Override public boolean onShowFileChooser(WebView view,ValueCallback<Uri[]> callback,FileChooserParams params){
                if(chooser!=null)chooser.onReceiveValue(null);chooser=callback;
                Intent pick=new Intent(Intent.ACTION_OPEN_DOCUMENT);pick.setType("audio/*");pick.addCategory(Intent.CATEGORY_OPENABLE);pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE,true);
                startActivityForResult(pick,11);return true;
            }
            @Override public void onPermissionRequest(PermissionRequest request){request.deny();}
        });
        web.addJavascriptInterface(new DownloadBridge(),"SonicDownload");
        web.addJavascriptInterface(new NetworkBridge(),"SonicNative");
        hardware=new HardwareController(this,web);web.addJavascriptInterface(hardware,"SonicHardware");
        web.setDownloadListener((url,agent,disposition,mime,size)->{
            if(!url.startsWith("blob:")&&!trusted(Uri.parse(url)))return;
            String script="fetch("+org.json.JSONObject.quote(url)+").then(r=>r.blob()).then(b=>{if(b.size>52428800)throw Error('文件超过50MB');const f=new FileReader();f.onload=()=>SonicDownload.save(f.result);f.readAsDataURL(b)}).catch(e=>alert(e.message))";
            web.evaluateJavascript(script,null);
        });
        authenticate();
    }
    private boolean trusted(Uri uri){Uri allowed=Uri.parse(base);return allowed.getScheme().equals(uri.getScheme())&&allowed.getHost().equals(uri.getHost())&&allowed.getPort()==uri.getPort();}
    private void grantAudio(){if(audioPermission!=null){audioPermission.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});audioPermission=null;}}
    @Override public void onRequestPermissionsResult(int code,String[] names,int[] results){super.onRequestPermissionsResult(code,names,results);if(code==101&&hardware!=null){boolean allowed=results.length>0;for(int result:results)allowed&=result==PackageManager.PERMISSION_GRANTED;if(allowed)hardware.permissionsResult();else hardware.permissionDenied();}if(code==12){if(results.length>0&&results[0]==PackageManager.PERMISSION_GRANTED)grantAudio();else if(audioPermission!=null){audioPermission.deny();audioPermission=null;}}}
    private void authenticate(){
        Toast.makeText(this,"正在连接 SONIC 服务器…",Toast.LENGTH_SHORT).show();
        new Thread(()->{
            HttpURLConnection connection=null;
            try{
                connection=(HttpURLConnection)new URL(base+"/api/app/login").openConnection();
                connection.setRequestMethod("POST");connection.setConnectTimeout(15000);connection.setReadTimeout(20000);
                connection.setRequestProperty("Authorization","Bearer "+BuildConfig.APP_ACCESS_TOKEN);
                if(connection.getResponseCode()!=200)throw new Exception("Login failed");
                final String cookie=connection.getHeaderField("Set-Cookie");
                if(cookie==null)throw new Exception("Missing session");
                runOnUiThread(()->{CookieManager.getInstance().setAcceptCookie(true);CookieManager.getInstance().setCookie(base,cookie,ok->{CookieManager.getInstance().flush();web.loadUrl(base+"/mobile/");});});
            }catch(Exception e){runOnUiThread(()->new AlertDialog.Builder(this).setTitle("服务器暂时不可用").setMessage("请检查手机网络，稍后点重新连接。若临时服务地址已更新，需要安装最新版本。").setPositiveButton("知道了",null).show());}
            finally{if(connection!=null)connection.disconnect();}
        }).start();
    }
    public class DownloadBridge {
        @JavascriptInterface public void save(String data){
            if(data.length()>71000000)return;
            try{byte[] bytes=Base64.decode(data.substring(data.indexOf(',')+1),Base64.DEFAULT);
                if(bytes.length>52428800)return;pendingDownload=bytes;
                runOnUiThread(()->{Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT);intent.addCategory(Intent.CATEGORY_OPENABLE);intent.setType("audio/wav");intent.putExtra(Intent.EXTRA_TITLE,"SONIC-"+System.currentTimeMillis()+".wav");startActivityForResult(intent,13);});
            }catch(Exception ignored){}
        }
    }
    public class NetworkBridge {
        @JavascriptInterface public void setUserToken(String token){if(token!=null&&token.length()<8192&&!token.contains("\n")&&!token.contains("\r")){NativeHttp.userToken=token;runOnUiThread(()->{String cookie="sonic_user="+token+"; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age="+(token.isEmpty()?"0":"2592000");CookieManager.getInstance().setCookie(base,cookie,ok->CookieManager.getInstance().flush());});}}
        @JavascriptInterface public void request(String id,String path,String method,String encoded,String contentType){
            if(destroyed||id==null||!id.matches("sonic_[0-9_]+")||encoded==null||encoded.length()>19000000)return;
            final String requestUser=NativeHttp.userToken;
            network.execute(()->{
                int status=0;String body="",type="",error="";
                try{
                    NativeHttp.Result result=NativeHttp.requestAs(base,BuildConfig.APP_ACCESS_TOKEN,method,path,Base64.decode(encoded,Base64.DEFAULT),contentType,requestUser);
                    status=result.status;body=Base64.encodeToString(result.body,Base64.NO_WRAP);type=result.contentType;
                }catch(java.net.SocketTimeoutException failure){error="连接服务器超时，请检查网络后重试。";}
                catch(javax.net.ssl.SSLException failure){error="安全连接失败，请检查手机时间和系统更新。";}
                catch(Exception failure){error="无法连接服务器，请检查手机网络并点重新连接。";}
                String callback="window.SonicTransport&&window.SonicTransport.complete("+org.json.JSONObject.quote(id)+","+status+","+org.json.JSONObject.quote(body)+","+org.json.JSONObject.quote(type)+","+org.json.JSONObject.quote(error)+")";
                runOnUiThread(()->{if(!destroyed)web.evaluateJavascript(callback,null);});
            });
        }
    }
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);
        if(request==11&&chooser!=null){Uri[] selected=null;if(result==RESULT_OK&&data!=null){if(data.getClipData()!=null){int n=data.getClipData().getItemCount();selected=new Uri[n];for(int i=0;i<n;i++)selected[i]=data.getClipData().getItemAt(i).getUri();}else if(data.getData()!=null)selected=new Uri[]{data.getData()};}chooser.onReceiveValue(selected);chooser=null;}
        if(request==13){try{if(result==RESULT_OK&&data!=null&&pendingDownload!=null){try(OutputStream out=getContentResolver().openOutputStream(data.getData())){out.write(pendingDownload);}Toast.makeText(this,"已保存",Toast.LENGTH_SHORT).show();}}catch(Exception e){Toast.makeText(this,"保存失败",Toast.LENGTH_SHORT).show();}finally{pendingDownload=null;}}
    }
    @Override public void onBackPressed(){if(web.canGoBack())web.goBack();else super.onBackPressed();}
    @Override protected void onDestroy(){destroyed=true;if(hardware!=null)hardware.close();network.shutdownNow();web.destroy();super.onDestroy();}
}
