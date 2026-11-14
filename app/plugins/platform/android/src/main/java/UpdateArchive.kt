package com.account.platform

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.ZipFile

/** Extracts one bounded APK to a fixed caller-owned path, never a ZIP-supplied destination. */
internal object UpdateArchive {
    fun extract(zip:File,candidate:JSONObject,destination:File,identifier:String):String {
        ZipFile(zip).use { archive ->
            val entries=archive.entries().toList()
            require(entries.size==2 && entries.map {it.name}.toSet().size==2)
            val metadata=archive.getEntry("manifest.json") ?: throw IllegalArgumentException("manifest")
            require(metadata.size in 1..128*1024L && !metadata.isDirectory)
            val bytes=ByteArrayOutputStream()
            archive.getInputStream(metadata).use {UpdatePolicy.copyVerified(it,bytes,128*1024L,null,metadata.size)}
            val component=JSONObject(bytes.toString("UTF-8"))
            require(!component.optBoolean("updateTestOnly",false)||candidate.optBoolean("updateTestOnly",false))
            require(component.getInt("format")==1 && component.getString("profile")=="preview" && component.getString("platform")=="android-arm64")
            require(component.getString("identifier")==identifier && component.getString("commit")==candidate.getString("commit"))
            require(component.getString("version")==candidate.getString("version") && component.getString("nativeVersion")==candidate.getString("nativeVersion"))
            require(component.getLong("androidVersionCode")==candidate.getLong("code"))
            val files=component.getJSONObject("files");require(files.length()==1)
            val name=files.keys().next();require(Regex("install/[A-Za-z0-9_.-]+\\.apk").matches(name) && !name.contains(".."))
            val hash=files.getString(name);require(UpdatePolicy.validHash(hash))
            val entry=archive.getEntry(name) ?: throw IllegalArgumentException("apk")
            require(entry.size in 1..UpdatePolicy.MAX_APK && !entry.isDirectory)
            archive.getInputStream(entry).use {input -> destination.outputStream().use {output ->
                UpdatePolicy.copyVerified(input,output,UpdatePolicy.MAX_APK,hash,entry.size)
            }}
            return hash
        }
    }
}
