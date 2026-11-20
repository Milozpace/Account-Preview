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
import java.io.FileNotFoundException
import java.util.zip.ZipFile
import java.util.concurrent.Executors

/** App binaries only. Never reads the ledger, pairing, vault or WebDAV credentials. */
internal class MobileUpdater(private val activity: Activity, compiledVersion: String) {
    private val source=UpdateSource.fromMarker(try {activity.assets.open(UpdateSource.MARKER).bufferedReader().use {it.readText()}}catch(_:FileNotFoundException){null})
    private val productVersion=source.productVersion?:compiledVersion
    private val preferences=activity.getSharedPreferences(if(source.isTest)"app-updates-test" else "app-updates",Activity.MODE_PRIVATE)
    private val executor=Executors.newSingleThreadExecutor()
    private val directory=File(activity.cacheDir,if(source.isTest)"app-updates/test" else "app-updates")
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
    private var manualCheckPending=false
    private var stableOnly=true

    init {
        // Revalidate asynchronously after process death; stale cache cannot become installable.
        if(supported && preferences.contains("ready")) {
            phase="checking"
            executor.execute {
                try {
                    val saved=preferences.getString("ready",null)
                    if(saved!=null && apk.isFile) {
                        val candidate=JSONObject(saved)
                        require(candidate.optString("sourceId","github-public-v1")==source.id)
                        if(candidate.getLong("code")>currentCode && UpdatePolicy.newer(candidate.getString("version"),productVersion)) {
                            val hash=candidate.getString("apkHash")
                            validateApk(candidate,hash)
                            synchronized(this){target=candidate;apkHash=hash;phase="ready"}
                        } else clearReady()
                    } else clearReady()
                } catch (_: Exception) {clearReady()}
                synchronized(this) {
                    if(!startPendingManualCheck()) {
                        if(phase=="checking")phase="idle"
                        action("auto")
                    }
                }
            }
        }
    }
    @Synchronized fun status(): JSONObject {
        val candidate=target
        val remind=candidate!=null && !manualResult && phase in setOf("available","ready") &&
            UpdatePolicy.shouldRemind(candidate.getString("version"),preferences.getString("ignored","")?:"",preferences.getLong("deferUntil",0),System.currentTimeMillis())
        val result=JSONObject().put("supported",supported).put("currentVersion",productVersion).put("currentCode",currentCode)
            .put("phase",phase).put("message",message).put("progress",progress).put("remind",remind)
            .put("testChannel",source.isTest).put("stableOnly",stableOnly)
        if(installState.isNotEmpty())result.put("installState",installState)
        // URLs, paths, commits and hash state are not accepted from or exposed to the WebView.
        if(candidate!=null) result.put("target",JSONObject().put("version",candidate.getString("version"))
            .put("nativeVersion",candidate.getString("nativeVersion")).put("code",candidate.getLong("code"))
            .put("notes",candidate.getString("notes")).put("size",candidate.getLong("size"))
            .put("prerelease",UpdatePolicy.isPrerelease(candidate.getString("version"))==true))
        return result
    }
    @Synchronized fun action(action: String): JSONObject {
        require(action in setOf("auto","check","download","install","later","ignore"))
        if(supported && phase=="checking" && action=="check") {
            if(!manualResult)manualCheckPending=true
            return status()
        }
        if(!supported || phase in setOf("checking","downloading"))return status()
        when(action) {
            "later" -> {preferences.edit().putLong("deferUntil",System.currentTimeMillis()+24*60*60*1000L).apply();manualResult=false}
            "ignore" -> {target?.let {preferences.edit().putString("ignored",it.getString("version")).apply()};manualResult=false}
            "check","auto" -> {
                val manual=action=="check"
                val now=System.currentTimeMillis()
                if(!UpdatePolicy.shouldCheck(manual,now,preferences.getLong("lastCheck",0)))return status()
                startCheck(manual,now)
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
    private fun startCheck(manual:Boolean,now:Long) {
        preferences.edit().putLong("lastCheck",now).apply()
        manualResult=manual;stableOnly=!manual;message="";installState="";phase="checking"
        executor.execute {check(manual)}
    }
    // A tap during a stable-only check must still fetch the prerelease catalog results.
    private fun startPendingManualCheck():Boolean {
        if(!manualCheckPending)return false
        manualCheckPending=false
        startCheck(true,System.currentTimeMillis())
        return true
    }
    private fun check(manual: Boolean) {
        try {
            val discovery=UpdateRelease.discoverAvailable(productVersion,currentCode,activity.packageName,source,
                {readBytes(it,2*1024*1024L)}, {url -> fetch(url,"HEAD"){it.contentLengthLong}},includePrereleases=manual)
            val candidate=discovery.candidate
            synchronized(this) {
                if(startPendingManualCheck())return
                val partial=discovery.prereleaseCheckUnavailable
                stableOnly=!manual||partial!=null
                message=if(partial==null)"" else "正式版已检查，预发布版暂时无法确认。"+(partial as? UpdateRateLimited)?.userMessage().orEmpty()
                if(candidate==null) {
                    // A stable-only check cannot invalidate a manually selected prerelease.
                    if(stableOnly&&target?.let {UpdatePolicy.isPrerelease(it.getString("version"))}==true) {
                        phase=if(apk.isFile&&apkHash.isNotEmpty())"ready" else "available"
                        return
                    }
                    clearReady();target=null;phase="latest"
                    return
                }
                // A recheck must not discard a fully validated package for the same target.
                val same=target?.optString("zipHash")==candidate.getString("zipHash") && apk.isFile && apkHash.isNotEmpty()
                if(!same)clearReady()
                target=candidate;phase=if(same)"ready" else "available"
            }
        } catch (error: Exception) {
            android.util.Log.w("AccountUpdate","check failed: ${error.javaClass.simpleName}")
            synchronized(this){
                if(startPendingManualCheck())return
                val showFailure=manual
                phase=UpdatePolicy.phaseAfterCheckFailure(showFailure,apkHash.isNotEmpty()&&apk.isFile,target!=null)
                message=if(showFailure)(error as? UpdateRateLimited)?.userMessage()
                    ?: "无法完成更新检查。请确认可访问 GitHub 接口及下载服务，稍后重试；也可使用 USB 安装。" else ""
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
        } catch (error: Exception) {
            android.util.Log.w("AccountUpdate","download failed: ${error.javaClass.simpleName}")
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
        if(!source.isTest)ZipFile(apk).use {require(it.getEntry("assets/${UpdateSource.MARKER}")==null)}
        val info=activity.packageManager.getPackageArchiveInfo(apk.absolutePath,signatureFlags()) ?: error("package")
        require(info.packageName==activity.packageName && code(info)==candidate.getLong("code") && code(info)>currentCode)
        require(info.versionName==candidate.getString("nativeVersion"))
        val installedSigners=signers(installed)
        require(installedSigners.isNotEmpty() && signers(info)==installedSigners)
    }
    // Only updater-owned cache files, never app data or shared external storage.
    @Synchronized private fun clearReady() {apk.delete();apkHash="";preferences.edit().remove("ready").apply()}
    private fun readBytes(url:String,limit:Long):ByteArray=fetch(url) { connection ->
        require(connection.contentLengthLong<=limit)
        val output=ByteArrayOutputStream()
        connection.inputStream.use {UpdatePolicy.copyVerified(it,output,limit,null)}
        output.toByteArray()
    }
    private fun <T> fetch(initial:String,method:String="GET",read:(HttpURLConnection)->T):T {
        var url=initial
        repeat(6) {
            require(source.allows(url))
            val connection=URL(url).openConnection() as HttpURLConnection
            try {
                connection.instanceFollowRedirects=false;connection.connectTimeout=15_000;connection.readTimeout=30_000
                connection.requestMethod=method
                connection.setRequestProperty("User-Agent","Account-Android-Updater")
                connection.setRequestProperty("Accept","application/vnd.github+json, application/octet-stream")
                val response=connection.responseCode
                if(response in setOf(301,302,303,307,308)) {
                    url=URL(URL(url),connection.getHeaderField("Location") ?: error("redirect")).toString()
                } else {
                    if(response!=200) {
                        android.util.Log.w("AccountUpdate","HTTP status $response")
                        val retryDelay=connection.getHeaderField("Retry-After")?.toLongOrNull()?.takeIf {it in 1..86400}
                        val quotaEmpty=connection.getHeaderField("X-RateLimit-Remaining")=="0"
                        if(URL(url).host=="api.github.com" && (response==429 || (response==403 && (quotaEmpty||retryDelay!=null)))) {
                            val retryAt=retryDelay?.let {System.currentTimeMillis()/1000+it}
                                ?: connection.getHeaderField("X-RateLimit-Reset")?.toLongOrNull()
                            throw UpdateRateLimited(retryAt)
                        }
                    }
                    require(response==200);return read(connection)
                }
            } finally {connection.disconnect()}
        }
        error("redirect limit")
    }
}
