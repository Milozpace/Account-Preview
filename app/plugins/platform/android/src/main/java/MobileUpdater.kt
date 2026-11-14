package com.account.platform

import android.app.Activity
import android.content.ClipData
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/** App binaries only. Never reads the ledger, pairing, vault or WebDAV credentials. */
internal class MobileUpdater(private val activity: Activity, private val productVersion: String) {
    private val preferences=activity.getSharedPreferences("app-updates",Activity.MODE_PRIVATE)
    private val executor=Executors.newSingleThreadExecutor()
    private val directory=File(activity.cacheDir,"app-updates")
    private val apk=File(directory,"update.apk")
    private val installed=packageInfo()
    private val currentCode=code(installed)
    private val supported=activity.packageName=="com.account.preview"
    private var phase="idle"
    private var message=""
    private var progress=0
    private var installState=""
    private var target: JSONObject?=null
    private var apkHash=""
    private var manualResult=false

    init {
        // Revalidate asynchronously after process death; stale cache cannot become installable.
        if(supported && preferences.contains("ready")) {
            phase="checking"
            executor.execute {
                try {
                    val saved=preferences.getString("ready",null)
                    if(saved!=null && apk.isFile) {
                        val candidate=JSONObject(saved)
                        if(candidate.getLong("code")>currentCode && UpdatePolicy.newerStable(candidate.getString("version"),productVersion)) {
                            val hash=candidate.getString("apkHash")
                            validateApk(candidate,hash)
                            synchronized(this){target=candidate;apkHash=hash;phase="ready"}
                        } else clearReady()
                    } else clearReady()
                } catch (_: Exception) {clearReady()}
                synchronized(this){if(phase=="checking")phase="idle"}
                action("auto")
            }
        }
    }
    @Synchronized fun status(): JSONObject {
        val candidate=target
        val remind=candidate!=null && !manualResult && phase in setOf("available","ready") &&
            UpdatePolicy.shouldRemind(candidate.getString("version"),preferences.getString("ignored","")?:"",preferences.getLong("deferUntil",0),System.currentTimeMillis())
        val result=JSONObject().put("supported",supported).put("currentVersion",productVersion).put("currentCode",currentCode)
            .put("phase",phase).put("message",message).put("progress",progress).put("remind",remind)
        if(installState.isNotEmpty())result.put("installState",installState)
        // URLs, paths, commits and hash state are not accepted from or exposed to the WebView.
        if(candidate!=null) result.put("target",JSONObject().put("version",candidate.getString("version"))
            .put("nativeVersion",candidate.getString("nativeVersion")).put("code",candidate.getLong("code"))
            .put("notes",candidate.getString("notes")).put("size",candidate.getLong("size")))
        return result
    }
    @Synchronized fun action(action: String): JSONObject {
        require(action in setOf("auto","check","download","install","later","ignore"))
        if(supported && phase=="checking" && action=="check") {manualResult=true;return status()}
        if(!supported || phase in setOf("checking","downloading"))return status()
        when(action) {
            "later" -> {preferences.edit().putLong("deferUntil",System.currentTimeMillis()+24*60*60*1000L).apply();manualResult=false}
            "ignore" -> {target?.let {preferences.edit().putString("ignored",it.getString("version")).apply()};manualResult=false}
            "check","auto" -> {
                val manual=action=="check"
                val now=System.currentTimeMillis()
                if(!UpdatePolicy.shouldCheck(manual,now,preferences.getLong("lastCheck",0)))return status()
                preferences.edit().putLong("lastCheck",now).apply()
                manualResult=manual;message="";installState="";phase="checking"
                executor.execute { check(manual) }
            }
            "download" -> {
                val candidate=target ?: return status()
                phase="downloading";message="";progress=0;installState="";manualResult=true
                executor.execute { download(candidate) }
            }
            "install" -> {
                if(phase!="ready")return status()
                // Verification is I/O; it must not block the UI or vault executor.
                phase="downloading";message="正在复核安装包…";progress=100
                executor.execute { install() }
            }
        }
        return status()
    }
    private fun check(manual: Boolean) {
        try {
            val candidate=UpdateRelease.discover(
                readJson("https://api.github.com/repos/${UpdateRelease.REPOSITORY}/releases?per_page=100"),
                productVersion,currentCode,activity.packageName
            ) {readBytes(it,2*1024*1024L)}
            if(candidate==null) {
                synchronized(this){clearReady();target=null;phase="latest";message=""}
                return
            }
            val zipHash=candidate.getString("zipHash")
            synchronized(this) {
                // A manual recheck must not discard a fully validated package for the same target.
                val same=target?.optString("zipHash")==zipHash && apk.isFile && apkHash.isNotEmpty()
                if(!same)clearReady()
                target=candidate;phase=if(same)"ready" else "available";message=""
            }
        } catch (_: Exception) {
            synchronized(this){
                val showFailure=manual||manualResult
                phase=UpdatePolicy.phaseAfterCheckFailure(showFailure,apkHash.isNotEmpty()&&apk.isFile,target!=null)
                message=if(showFailure)"无法完成更新检查。请确认可访问 GitHub 接口及下载服务，稍后重试；也可使用 USB 安装。" else ""
            }
        }
    }
    private fun download(candidate: JSONObject) {
        val zip=File(directory,"component.part")
        val partial=File(directory,"update.part")
        try {
            require(directory.mkdirs()||directory.isDirectory)
            clearReady()
            fetch(candidate.getString("url")) { connection ->
                val length=connection.contentLengthLong
                require(length<0 || length==candidate.getLong("size"))
                val deadline=System.nanoTime()+10*60*1_000_000_000L
                connection.inputStream.use { input -> zip.outputStream().use { output ->
                    UpdatePolicy.copyVerified(input,output,UpdatePolicy.MAX_ZIP,candidate.getString("zipHash"),candidate.getLong("size")) { count ->
                        require(System.nanoTime()<deadline)
                        synchronized(this){progress=(count*90/candidate.getLong("size")).toInt()}
                    }
                }}
            }
            val hash=UpdateArchive.extract(zip,candidate,partial,activity.packageName)
            synchronized(this){apkHash=hash}
            require(partial.renameTo(apk))
            validateApk(candidate)
            synchronized(this) {
                target=candidate;phase="ready";progress=100;message=""
                preferences.edit().putString("ready",JSONObject(candidate.toString()).put("apkHash",apkHash).toString()).apply()
            }
        } catch (_: Exception) {
            synchronized(this){clearReady();phase="error";message="下载或安装包校验失败，请检查网络和可用空间后重新下载。"}
        } finally {zip.delete();partial.delete()}
    }
    private fun install() {
        try {
            val candidate=synchronized(this){target ?: error("target")}
            validateApk(candidate)
            activity.runOnUiThread {
                try {
                    if(Build.VERSION.SDK_INT>=26 && !activity.packageManager.canRequestPackageInstalls()) {
                        activity.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:${activity.packageName}")))
                        synchronized(this){phase="ready";message="";installState="permission"}
                    } else {
                        val uri=FileProvider.getUriForFile(activity,"${activity.packageName}.updates",apk)
                        val intent=Intent(Intent.ACTION_VIEW).setDataAndType(uri,"application/vnd.android.package-archive")
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        intent.clipData=ClipData.newRawUri("Account update",uri)
                        activity.startActivity(intent)
                        synchronized(this){phase="ready";message="";installState="opened"}
                    }
                } catch (_: Exception) {synchronized(this){phase="ready";message="未能打开系统安装界面，请重试或使用 USB 安装。"}}
            }
        } catch (_: Exception) {synchronized(this){clearReady();phase="error";message="安装包已失效，请重新下载。"}}
    }
    @Suppress("DEPRECATION") private fun packageInfo(): PackageInfo = activity.packageManager.getPackageInfo(activity.packageName,signatureFlags())
    @Suppress("DEPRECATION") private fun signatureFlags(): Int = if(Build.VERSION.SDK_INT>=28)PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
    @Suppress("DEPRECATION") private fun code(info: PackageInfo):Long=if(Build.VERSION.SDK_INT>=28)info.longVersionCode else info.versionCode.toLong()
    @Suppress("DEPRECATION") private fun signers(info:PackageInfo):Set<String> {
        val signatures=if(Build.VERSION.SDK_INT>=28) info.signingInfo?.apkContentsSigners else info.signatures
        return signatures?.map {UpdatePolicy.digest(it.toByteArray())}?.toSet() ?: emptySet()
    }
    @Suppress("DEPRECATION") private fun validateApk(candidate:JSONObject,expectedHash:String=apkHash) {
        require(apk.isFile && apk.length() in 1..UpdatePolicy.MAX_APK)
        apk.inputStream().use {UpdatePolicy.copyVerified(it,object:java.io.OutputStream(){override fun write(b:Int){};override fun write(b:ByteArray,off:Int,len:Int){}},UpdatePolicy.MAX_APK,expectedHash)}
        val info=activity.packageManager.getPackageArchiveInfo(apk.absolutePath,signatureFlags()) ?: error("package")
        require(info.packageName==activity.packageName && code(info)==candidate.getLong("code") && code(info)>currentCode)
        require(info.versionName==candidate.getString("nativeVersion"))
        val installedSigners=signers(installed)
        require(installedSigners.isNotEmpty() && signers(info)==installedSigners)
    }
    // Only updater-owned cache files, never app data or shared external storage.
    @Synchronized private fun clearReady() {apk.delete();apkHash="";preferences.edit().remove("ready").apply()}
    private fun readJson(url:String):String=String(readBytes(url,2*1024*1024L),Charsets.UTF_8)
    private fun readBytes(url:String,limit:Long):ByteArray=fetch(url) { connection ->
        require(connection.contentLengthLong<=limit)
        val output=ByteArrayOutputStream()
        connection.inputStream.use {UpdatePolicy.copyVerified(it,output,limit,null)}
        output.toByteArray()
    }
    private fun <T> fetch(initial:String,read:(HttpURLConnection)->T):T {
        var url=initial
        repeat(6) {
            require(UpdatePolicy.allowedUrl(url))
            val connection=URL(url).openConnection() as HttpURLConnection
            try {
                connection.instanceFollowRedirects=false;connection.connectTimeout=15_000;connection.readTimeout=30_000
                connection.setRequestProperty("User-Agent","Account-Android-Updater")
                connection.setRequestProperty("Accept","application/vnd.github+json, application/octet-stream")
                val response=connection.responseCode
                if(response in setOf(301,302,303,307,308)) {
                    url=URL(URL(url),connection.getHeaderField("Location") ?: error("redirect")).toString()
                } else {require(response==200);return read(connection)}
            } finally {connection.disconnect()}
        }
        error("redirect limit")
    }
}
