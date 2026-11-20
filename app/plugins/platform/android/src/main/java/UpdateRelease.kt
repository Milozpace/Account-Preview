package com.account.platform

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

internal class UpdateRateLimited(private val retryAtSeconds:Long?) : IOException("GitHub update catalog rate limit") {
    fun userMessage(nowSeconds:Long=System.currentTimeMillis()/1000):String {
        val retry=retryAtSeconds?.takeIf {it>nowSeconds && it-nowSeconds<=24*60*60}
        if(retry==null)return "GitHub 版本列表暂时限流，请稍后重试。"
        val minute=(retry+(60-retry%60)%60)*1000
        val time=SimpleDateFormat("HH:mm",Locale.ROOT).format(Date(minute))
        return "GitHub 版本列表暂时限流，请于 ${time} 后重试。"
    }
}

internal data class UpdateDiscovery(val candidate:JSONObject?,val prereleaseCheckUnavailable:Exception?=null)

/** Release/manifest contract kept independent of Android and tested with synthetic fixtures. */
internal object UpdateRelease {
    const val REPOSITORY="Milozpace/Account-Preview"
    fun discover(index:String,currentVersion:String,currentCode:Long,identifier:String,source:UpdateSource=UpdateSource.fromMarker(null),
        includePrereleases:Boolean=false,read:(String)->ByteArray):JSONObject? =
        discoverCatalog(JSONArray(index),currentVersion,currentCode,identifier,source,includePrereleases,read)

    private fun discoverCatalog(releases:JSONArray,currentVersion:String,currentCode:Long,identifier:String,source:UpdateSource,
        includePrereleases:Boolean,read:(String)->ByteArray):JSONObject? {
        var selected:JSONObject?=null
        for(i in 0 until releases.length()) {
            val release=releases.getJSONObject(i)
            if(release.optBoolean("draft",true))continue
            val prerelease=release.opt("prerelease") as? Boolean ?: continue
            val version=release.optString("tag_name").removePrefix("v")
            val newer=if(includePrereleases)UpdatePolicy.newer(version,currentVersion) else UpdatePolicy.newerStable(version,currentVersion)
            if(release.optString("tag_name")!="v$version" || UpdatePolicy.isPrerelease(version)!=prerelease || !newer)continue
            if(selected==null || UpdatePolicy.newer(version,selected.getString("tag_name").removePrefix("v")))selected=release
        }
        if(selected==null)return null
        val version=selected.getString("tag_name").removePrefix("v")
        val assets=selected.getJSONArray("assets")
        val manifestAsset=asset(assets,"release-manifest.json")
        val manifestBytes=read(assetUrl(manifestAsset,source))
        val digest=manifestAsset.optString("digest")
        if(digest.startsWith("sha256:"))require(UpdatePolicy.digest(manifestBytes)==digest.removePrefix("sha256:"))
        val manifest=JSONObject(String(manifestBytes,Charsets.UTF_8))
        return candidate(manifest,version,currentVersion,currentCode,identifier,source,selected.optString("body")) {name ->
            val item=asset(assets,name)
            Pair(assetUrl(item,source),item.getLong("size"))
        }
    }
    const val LATEST_MANIFEST="https://github.com/$REPOSITORY/releases/latest/download/release-manifest.json"
    fun discoverAvailable(currentVersion:String,currentCode:Long,identifier:String,source:UpdateSource,
        read:(String)->ByteArray,size:(String)->Long,includePrereleases:Boolean=false):UpdateDiscovery {
        val staticCatalog=source.staticCatalogUrl?.let {url ->
            try {
                val index=JSONObject(String(read(url),Charsets.UTF_8))
                require(index.getInt("format")==1)
                index.getJSONArray("releases")
            } catch (_:Exception) {null}
        }
        val catalog=staticCatalog ?: try {
            JSONArray(String(read(source.catalogUrl),Charsets.UTF_8))
        } catch(error:Exception) {
            if(source.isTest)throw error
            val stable=try {
                latestStable(currentVersion,currentCode,identifier,source,read,size)
            } catch (fallbackError:Exception) {
                throw if(error is UpdateRateLimited)error else fallbackError
            }
            return UpdateDiscovery(stable,error.takeIf {includePrereleases})
        }
        return UpdateDiscovery(discoverCatalog(catalog,currentVersion,currentCode,identifier,source,includePrereleases,read))
    }

    private fun latestStable(currentVersion:String,currentCode:Long,identifier:String,source:UpdateSource,
        read:(String)->ByteArray,size:(String)->Long):JSONObject? {
        val manifest=JSONObject(String(read(LATEST_MANIFEST),Charsets.UTF_8))
        val version=manifest.getString("version")
        require(UpdatePolicy.isPrerelease(version)==false)
        return candidate(manifest,version,currentVersion,currentCode,identifier,source,
            manifest.optString("notes").ifBlank {"暂未读取到更新说明，可在 GitHub 发布页查看。"}) {name ->
            require(Regex("[A-Za-z0-9_.-]+\\.zip").matches(name)&&!name.contains(".."))
            val url="https://github.com/$REPOSITORY/releases/download/v$version/$name"
            Pair(url,size(url))
        }
    }
    private fun candidate(manifest:JSONObject,version:String,currentVersion:String,currentCode:Long,identifier:String,
        source:UpdateSource,notes:String,assetInfo:(String)->Pair<String,Long>):JSONObject? {
        require(manifest.getInt("format")==1 && manifest.getString("distribution")=="public")
        require(!manifest.optBoolean("updateTestOnly",false)||source.isTest)
        require(manifest.getString("version")==version && manifest.getString("tag")=="v$version")
        if(!UpdatePolicy.newer(version,currentVersion))return null
        val nextCode=manifest.getLong("androidVersionCode")
        require(nextCode>currentCode && nextCode<=Int.MAX_VALUE)
        val commit=manifest.getString("commit");require(Regex("[0-9a-f]{40}").matches(commit))
        val profile=manifest.getJSONObject("profiles").getJSONObject("preview")
        require(profile.getString("identifier")==identifier)
        val platform=profile.getJSONObject("platforms").getJSONObject("android-arm64")
        val zipHash=platform.getString("sha256");require(UpdatePolicy.validHash(zipHash))
        val (url,bytes)=assetInfo(platform.getString("asset"))
        require(source.assetAllowed(url)&&bytes in 1..UpdatePolicy.MAX_ZIP)
        val appNotes=(manifest.opt("appNotes") as? String)?.takeIf {it.isNotBlank()} ?: notes
        return JSONObject().put("version",version).put("nativeVersion",manifest.getString("nativeVersion"))
            .put("code",nextCode).put("commit",commit).put("zipHash",zipHash).put("url",url)
            .put("size",bytes).put("notes",appNotes.take(40_000)).put("sourceId",source.id).put("updateTestOnly",source.isTest)
    }
    private fun asset(assets:JSONArray,name:String):JSONObject {
        val matching=(0 until assets.length()).map{assets.getJSONObject(it)}.filter{it.getString("name")==name}
        require(matching.size==1);return matching.single()
    }
    private fun assetUrl(asset:JSONObject,source:UpdateSource):String {
        val url=asset.getString("browser_download_url")
        require(source.assetAllowed(url))
        return url
    }
}
