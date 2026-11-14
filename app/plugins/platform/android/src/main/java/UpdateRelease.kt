package com.account.platform

import org.json.JSONArray
import org.json.JSONObject

/** Release/manifest contract kept independent of Android and tested with synthetic fixtures. */
internal object UpdateRelease {
    const val REPOSITORY="Milozpace/Account-Preview"
    fun discover(index:String,currentVersion:String,currentCode:Long,identifier:String,source:UpdateSource=UpdateSource.fromMarker(null),read:(String)->ByteArray):JSONObject? {
        val releases=JSONArray(index);var selected:JSONObject?=null
        for(i in 0 until releases.length()) {
            val release=releases.getJSONObject(i)
            if(release.optBoolean("draft",true)||release.optBoolean("prerelease",true))continue
            val version=release.optString("tag_name").removePrefix("v")
            if(release.optString("tag_name")!="v$version" || !UpdatePolicy.newerStable(version,currentVersion))continue
            if(selected==null || UpdatePolicy.newerStable(version,selected.getString("tag_name").removePrefix("v")))selected=release
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
        read:(String)->ByteArray,size:(String)->Long):JSONObject? {
        val index=try {read(source.catalogUrl)} catch(error:Exception) {
            if(source.isTest)throw error
            val manifest=JSONObject(String(read(LATEST_MANIFEST),Charsets.UTF_8))
            val version=manifest.getString("version")
            return candidate(manifest,version,currentVersion,currentCode,identifier,source,
                manifest.optString("notes").ifBlank {"暂未读取到更新说明，可在 GitHub 发布页查看。"}) {name ->
                require(Regex("[A-Za-z0-9_.-]+\\.zip").matches(name)&&!name.contains(".."))
                val url="https://github.com/$REPOSITORY/releases/download/v$version/$name"
                Pair(url,size(url))
            }
        }
        return discover(String(index,Charsets.UTF_8),currentVersion,currentCode,identifier,source,read)
    }
    private fun candidate(manifest:JSONObject,version:String,currentVersion:String,currentCode:Long,identifier:String,
        source:UpdateSource,notes:String,assetInfo:(String)->Pair<String,Long>):JSONObject? {
        require(manifest.getInt("format")==1 && manifest.getString("distribution")=="public")
        require(!manifest.optBoolean("updateTestOnly",false)||source.isTest)
        require(UpdatePolicy.stableKey(version)!=null && manifest.getString("version")==version && manifest.getString("tag")=="v$version")
        if(!UpdatePolicy.newerStable(version,currentVersion))return null
        val nextCode=manifest.getLong("androidVersionCode")
        require(nextCode>currentCode && nextCode<=Int.MAX_VALUE)
        val commit=manifest.getString("commit");require(Regex("[0-9a-f]{40}").matches(commit))
        val profile=manifest.getJSONObject("profiles").getJSONObject("preview")
        require(profile.getString("identifier")==identifier)
        val platform=profile.getJSONObject("platforms").getJSONObject("android-arm64")
        val zipHash=platform.getString("sha256");require(UpdatePolicy.validHash(zipHash))
        val (url,bytes)=assetInfo(platform.getString("asset"))
        require(source.assetAllowed(url)&&bytes in 1..UpdatePolicy.MAX_ZIP)
        return JSONObject().put("version",version).put("nativeVersion",manifest.getString("nativeVersion"))
            .put("code",nextCode).put("commit",commit).put("zipHash",zipHash).put("url",url)
            .put("size",bytes).put("notes",notes.take(40_000)).put("sourceId",source.id).put("updateTestOnly",source.isTest)
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
