package com.account.platform

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class UpdateReleaseTest {
    private val commit="a".repeat(40)
    private val bytes="synthetic-apk".toByteArray()
    private fun release(version:String,pre:Boolean=false,draft:Boolean=false)=JSONObject().put("tag_name","v$version").put("prerelease",pre).put("draft",draft)
        .put("body","合成更新说明").put("assets",JSONArray().put(asset("release-manifest.json",100,version)).put(asset("android.zip",500,version)))
    private fun asset(name:String,size:Int,version:String)=JSONObject().put("name",name).put("size",size).put("browser_download_url","https://github.com/Milozpace/Account-Preview/releases/download/v$version/$name")
    private fun manifest(version:String="0.4.1")=JSONObject().put("format",1).put("distribution","public").put("version",version).put("tag","v$version").put("androidVersionCode",4008)
        .put("commit",commit).put("nativeVersion","0.4.8").put("profiles",JSONObject().put("preview",JSONObject().put("identifier","com.account.preview")
            .put("platforms",JSONObject().put("android-arm64",JSONObject().put("asset","android.zip").put("sha256","b".repeat(64))))))
    private fun discover(releases:JSONArray=JSONArray().put(release("0.4.1")),metadata:JSONObject=manifest()):JSONObject? =
        UpdateRelease.discover(releases.toString(),"0.4.0",4007,"com.account.preview") {metadata.toString().toByteArray()}
    @Test fun selectsHighestStableAndMatchesRealReleaseManifestShape() {
        val index=JSONArray().put(release("0.4.9")).put(release("0.4.10")).put(release("1.0.0-rc.1",true)).put(release("2.0.0",false,true))
        val candidate=discover(index,manifest("0.4.10"))!!
        assertEquals("0.4.10",candidate.getString("version"));assertEquals(4008,candidate.getInt("code"));assertEquals(commit,candidate.getString("commit"))
        assertEquals("合成更新说明",candidate.getString("notes"))
        assertNull(discover(JSONArray().put(release("0.4.0"))))
    }
    @Test fun prefersTheVersionBoundUserNotesOverTheFullReleaseBody() {
        val metadata=manifest().put("appNotes","- 查账更顺手")
        assertEquals("- 查账更顺手",discover(metadata=metadata)!!.getString("notes"))
    }
    @Test fun autoFindsTheStableSuccessorFromAnRcAndManualCanChooseTheNewestPrerelease() {
        val index=JSONArray().put(release("0.4.2-rc.9",true)).put(release("0.4.2-rc.10",true))
            .put(release("0.4.1")).put(release("0.5.0-rc.1",true,true))
            .put(release("0.6.0-rc.1")).put(release("0.7.0",true))
        fun check(manual:Boolean):JSONObject? = UpdateRelease.discoverAvailable(
            "0.4.1-rc.1",4007,"com.account.preview",UpdateSource.fromMarker(null),{url ->
                if(url.startsWith("https://api.github.com/"))index.toString().toByteArray()
                else manifest(url.substringAfter("/download/v").substringBefore('/')).put("appNotes","- 版本说明").toString().toByteArray()
            },{ fail("catalog already provides asset size");0L },includePrereleases=manual)
        assertEquals("0.4.1",check(false)!!.getString("version"))
        val prerelease=check(true)!!
        assertEquals("0.4.2-rc.10",prerelease.getString("version"))
        assertEquals("- 版本说明",prerelease.getString("notes"))
    }
    @Test fun stableBuildCanManuallyFindRcButAutomaticCheckLeavesItAlone() {
        val index=JSONArray().put(release("0.4.2-rc.1",true))
        val metadata=manifest("0.4.2-rc.1").toString().toByteArray()
        assertNull(discover(index,manifest("0.4.2-rc.1")))
        val result=UpdateRelease.discover(index.toString(),"0.4.1",4007,"com.account.preview",includePrereleases=true){metadata}
        assertEquals("0.4.2-rc.1",result!!.getString("version"))
    }
    @Test fun rejectsDowngradeIdentityTagCommitAndTamperedManifest() {
        val mutations=listOf<(JSONObject)->Unit>(
            {it.put("androidVersionCode",4007)}, {it.put("tag","v0.5.0")}, {it.put("commit","no-commit")},
            {it.put("distribution","personal-local")}, {it.getJSONObject("profiles").getJSONObject("preview").put("identifier","com.account.probe")})
        for(mutate in mutations) {val metadata=manifest();mutate(metadata);try{discover(metadata=metadata);fail("must reject") }catch(_:IllegalArgumentException) {}}
        val item=release("0.4.1");item.getJSONArray("assets").getJSONObject(0).put("digest","sha256:"+"0".repeat(64))
        try{discover(JSONArray().put(item));fail("manifest hash") }catch(_:IllegalArgumentException) {}
    }
    @Test fun onlyExplicitSignedTestSourceAcceptsMarkedRawBranchFixtures() {
        val source=UpdateSource.fromMarker(JSONObject().put("format",1).put("updateTestOnly",true).put("branch","codex/update-download-test-20261114").put("productVersion","0.4.0").toString())
        val item=release("0.4.1")
        val assets=item.getJSONArray("assets")
        for(i in 0 until assets.length()) {val asset=assets.getJSONObject(i);asset.put("browser_download_url","https://raw.githubusercontent.com/Milozpace/Account-Preview/codex/update-download-test-20261114/"+asset.getString("name"))}
        val metadata=manifest().put("updateTestOnly",true)
        val index=JSONArray().put(item).toString()
        val result=UpdateRelease.discover(index,"0.4.0",4007,"com.account.preview",source) {metadata.toString().toByteArray()}!!
        assertTrue(result.getBoolean("updateTestOnly"));assertEquals(source.id,result.getString("sourceId"))
        try {UpdateRelease.discover(index,"0.4.0",4007,"com.account.preview") {metadata.toString().toByteArray()};fail("production refuses test source") }catch(_:IllegalArgumentException){}
        try {discover(metadata=metadata);fail("production refuses test marker") }catch(_:IllegalArgumentException){}
    }
    @Test fun exhaustedAnonymousApiFallsBackToLatestPublishedManifest() {
        val calls=mutableListOf<String>()
        val result=UpdateRelease.discoverAvailable("0.4.0",4007,"com.account.preview",UpdateSource.fromMarker(null),{url ->
            calls.add(url)
            if(url.startsWith("https://api.github.com/"))throw java.io.IOException("synthetic rate limit")
            assertEquals(UpdateRelease.LATEST_MANIFEST,url)
            manifest().put("appNotes","- 即使回退也有更新说明").toString().toByteArray()
        },{url -> assertTrue(url.endsWith("/v0.4.1/android.zip"));500L})!!
        assertEquals("0.4.1",result.getString("version"));assertEquals(500L,result.getLong("size"))
        assertEquals("- 即使回退也有更新说明",result.getString("notes"))
        assertEquals(2,calls.size)
    }
    @Test fun latestFallbackStillRejectsTestBuildAndDoesNotDownloadWhenCurrent() {
        fun read(url:String,metadata:JSONObject):ByteArray {
            if(url.startsWith("https://api.github.com/"))throw java.io.IOException("synthetic rate limit")
            return metadata.toString().toByteArray()
        }
        assertNull(UpdateRelease.discoverAvailable("0.4.1",4008,"com.account.preview",UpdateSource.fromMarker(null),{read(it,manifest())},{fail("no package probe when latest");0L}))
        try {
            UpdateRelease.discoverAvailable("0.4.1",4008,"com.account.preview",UpdateSource.fromMarker(null),
                {read(it,manifest())},{fail("no package probe when latest");0L},includePrereleases=true)
            fail("stable fallback cannot say a manual check found no prereleases")
        } catch (_:java.io.IOException) {}
        try {UpdateRelease.discoverAvailable("0.4.0",4007,"com.account.preview",UpdateSource.fromMarker(null),{read(it,manifest().put("updateTestOnly",true))},{500L});fail("no test fallback") }catch(_:IllegalArgumentException){}
    }
    private fun component()=JSONObject().put("format",1).put("version","0.4.1").put("nativeVersion","0.4.8").put("androidVersionCode",4008)
        .put("commit",commit).put("identifier","com.account.preview").put("profile","preview").put("platform","android-arm64")
        .put("files",JSONObject().put("install/update.apk",UpdatePolicy.digest(bytes)))
    private fun withArchive(metadata:JSONObject=component(),extra:Boolean=false,body:ByteArray=bytes,run:(File,File)->Unit) {
        val dir=kotlin.io.path.createTempDirectory("synthetic-update").toFile()
        try {
            val archive=File(dir,"component.zip")
            ZipOutputStream(archive.outputStream()).use {zip ->
                for((name,value) in listOf("manifest.json" to metadata.toString().toByteArray(),"install/update.apk" to body)) {
                    zip.putNextEntry(ZipEntry(name));zip.write(value);zip.closeEntry()
                }
                if(extra){zip.putNextEntry(ZipEntry("extra"));zip.write(1);zip.closeEntry()}
            }
            run(archive,File(dir,"update.apk"))
        } finally {dir.deleteRecursively()}
    }
    @Test fun extractsOnlyMatchingApkFromTwoEntryBundle() {
        withArchive {archive,apk ->
            val hash=UpdateArchive.extract(archive,discover()!!,apk,"com.account.preview")
            assertEquals(UpdatePolicy.digest(bytes),hash);assertArrayEquals(bytes,apk.readBytes())
        }
    }
    @Test fun rejectsWrongCommitProfileCodeHashExtraEntryAndPayload() {
        val mutations=listOf<(JSONObject)->Unit>({it.put("commit","c".repeat(40))},{it.put("profile","personal")},{it.put("androidVersionCode",4009)},
            {it.getJSONObject("files").put("install/update.apk","0".repeat(64))})
        for(mutate in mutations) {val metadata=component();mutate(metadata);withArchive(metadata) {archive,apk ->
            try{UpdateArchive.extract(archive,discover()!!,apk,"com.account.preview");fail("must reject")}catch(_:IllegalArgumentException){}
        }}
        withArchive(extra=true) {archive,apk -> try{UpdateArchive.extract(archive,discover()!!,apk,"com.account.preview");fail("extra")}catch(_:IllegalArgumentException){} }
        withArchive(body="corrupt".toByteArray()) {archive,apk -> try{UpdateArchive.extract(archive,discover()!!,apk,"com.account.preview");fail("hash")}catch(_:IllegalArgumentException){} }
    }
}
