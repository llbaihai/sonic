package com.sonic.soundstudio;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;

/** Same-origin native requests. No redirects and no automatic application retries. */
final class NativeHttp {
    static volatile String userToken="";
    static final class Result {
        final int status; final byte[] body; final String contentType;
        Result(int status,byte[] body,String type){this.status=status;this.body=body;contentType=type;}
    }
    static Result request(String base,String token,String method,String path,byte[] body,String contentType) throws Exception {
        return requestAs(base,token,method,path,body,contentType,userToken);
    }
    static Result requestAs(String base,String token,String method,String path,byte[] body,String contentType,String requestUser) throws Exception {
        if(path==null||!path.startsWith("/")||path.startsWith("//")||path.contains("\r")||path.contains("\n"))throw new IllegalArgumentException("Invalid request path");
        URI origin=URI.create(base),target=origin.resolve(path);
        if(!origin.getScheme().equals(target.getScheme())||!origin.getHost().equals(target.getHost())||origin.getPort()!=target.getPort())throw new IllegalArgumentException("Foreign request blocked");
        if(!method.matches("GET|POST|PUT|PATCH|DELETE"))throw new IllegalArgumentException("Unsupported request method");
        if(body.length>13*1024*1024||contentType.contains("\r")||contentType.contains("\n"))throw new IllegalArgumentException("Invalid request body");
        HttpURLConnection connection=(HttpURLConnection)target.toURL().openConnection();
        try{
            connection.setInstanceFollowRedirects(false);connection.setRequestMethod(method.equals("PATCH")?"POST":method);
            if(method.equals("PATCH"))connection.setRequestProperty("X-HTTP-Method-Override","PATCH");
            connection.setConnectTimeout(15000);connection.setReadTimeout(150000);
            connection.setRequestProperty("Authorization","Bearer "+token);
            if(!requestUser.isEmpty())connection.setRequestProperty("X-Sonic-User-Token",requestUser);
            connection.setRequestProperty("User-Agent","SONIC-Android/0.4");
            if(!contentType.isEmpty())connection.setRequestProperty("Content-Type",contentType);
            if(!method.equals("GET")){
                connection.setDoOutput(true);connection.setFixedLengthStreamingMode(body.length);
                try(java.io.OutputStream output=connection.getOutputStream()){output.write(body);}
            }
            int status=connection.getResponseCode();
            InputStream input=status>=400?connection.getErrorStream():connection.getInputStream();
            ByteArrayOutputStream output=new ByteArrayOutputStream();
            if(input!=null)try(InputStream stream=input){byte[] chunk=new byte[16384];int count;
                while((count=stream.read(chunk))!=-1){if(output.size()+count>16*1024*1024)throw new IllegalArgumentException("Response exceeds 16MB");output.write(chunk,0,count);}
            }
            String type=connection.getContentType();return new Result(status,output.toByteArray(),type==null?"application/octet-stream":type);
        }finally{connection.disconnect();}
    }
}
