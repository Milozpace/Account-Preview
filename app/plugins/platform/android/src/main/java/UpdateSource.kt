package com.account.platform

import java.net.URI
import org.json.JSONObject

/** Test configuration can only come from a signed APK asset, never WebView input. */
internal class UpdateSource private constructor(private val prefix:String?,val productVersion:String?) {
    val isTest:Boolean get()=prefix!=null
    val id:String get()=prefix?:"github-public-v1"
    val catalogUrl:String get()=prefix?.plus("releases.json")?:"https://api.github.com/repos/${UpdateRelease.REPOSITORY}/releases?per_page=100"
    val staticCatalogUrl:String? get()=if(isTest)null else PUBLIC_CATALOG
    fun allows(value:String):Boolean=UpdatePolicy.allowedUrl(value)||value==staticCatalogUrl||testAsset(value)
    fun assetAllowed(value:String):Boolean=if(isTest)testAsset(value) else
        UpdatePolicy.allowedUrl(value)&&value.startsWith("https://github.com/${UpdateRelease.REPOSITORY}/releases/download/")
    private fun testAsset(value:String):Boolean {
        val base=prefix?:return false
        if(!value.startsWith(base))return false
        return try {
            val uri=URI(value)
            uri.scheme=="https"&&uri.host=="raw.githubusercontent.com"&&uri.port==-1&&uri.userInfo==null&&uri.fragment==null&&uri.query==null&&
                Regex("[A-Za-z0-9_.-]+").matches(value.removePrefix(base))&&!value.removePrefix(base).contains("..")
        } catch(_:Exception){false}
    }
    companion object {
        const val MARKER="account-update-test.json"
        const val PUBLIC_CATALOG="https://raw.githubusercontent.com/${UpdateRelease.REPOSITORY}/android-update-index/android-updates.json"
        fun fromMarker(marker:String?):UpdateSource {
            if(marker==null)return UpdateSource(null,null)
            val data=JSONObject(marker)
            require(data.getInt("format")==1&&data.getBoolean("updateTestOnly"))
            val branch=data.getString("branch")
            require(Regex("codex/update-download-test-[0-9]{8}").matches(branch))
            val version=data.getString("productVersion");require(UpdatePolicy.isPrerelease(version)==false)
            return UpdateSource("https://raw.githubusercontent.com/${UpdateRelease.REPOSITORY}/$branch/",version)
        }
    }
}
