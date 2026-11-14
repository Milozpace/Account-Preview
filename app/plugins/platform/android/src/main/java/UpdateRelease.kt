package com.account.platform

import org.json.JSONArray
import org.json.JSONObject

/** Release/manifest contract kept independent of Android and tested with synthetic fixtures. */
internal object UpdateRelease {
    const val REPOSITORY="Milozpace/Account-Preview"
    fun discover(index:String,currentVersion:String,currentCode:Long,identifier:String,read:(String)->ByteArray):JSONObject? {
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
        val manifestBytes=read(assetUrl(manifestAsset))
        val digest=manifestAsset.optString("digest")
        if(digest.startsWith("sha256:"))require(UpdatePolicy.digest(manifestBytes)==digest.removePrefix("sha256:"))
        val manifest=JSONObject(String(manifestBytes,Charsets.UTF_8))
        require(manifest.getInt("format")==1 && manifest.getString("distribution")=="public")
        require(manifest.getString("version")==version && manifest.getString("tag")=="v$version")
        val nextCode=manifest.getLong("androidVersionCode")
        require(nextCode>currentCode && nextCode<=Int.MAX_VALUE)
        val commit=manifest.getString("commit");require(Regex("[0-9a-f]{40}").matches(commit))
        val profile=manifest.getJSONObject("profiles").getJSONObject("preview")
        require(profile.getString("identifier")==identifier)
        val platform=profile.getJSONObject("platforms").getJSONObject("android-arm64")
        val zipHash=platform.getString("sha256");require(UpdatePolicy.validHash(zipHash))
        val zipAsset=asset(assets,platform.getString("asset"))
        val size=zipAsset.getLong("size");require(size in 1..UpdatePolicy.MAX_ZIP)
        return JSONObject().put("version",version).put("nativeVersion",manifest.getString("nativeVersion"))
            .put("code",nextCode).put("commit",commit).put("zipHash",zipHash).put("url",assetUrl(zipAsset))
            .put("size",size).put("notes",selected.optString("body").take(40_000))
    }
    private fun asset(assets:JSONArray,name:String):JSONObject {
        val matching=(0 until assets.length()).map{assets.getJSONObject(it)}.filter{it.getString("name")==name}
        require(matching.size==1);return matching.single()
    }
    private fun assetUrl(asset:JSONObject):String {
        val url=asset.getString("browser_download_url")
        require(UpdatePolicy.allowedUrl(url) && url.startsWith("https://github.com/$REPOSITORY/releases/download/"))
        return url
    }
}
