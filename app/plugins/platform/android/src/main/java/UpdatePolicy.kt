package com.account.platform

import java.net.URI
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest

/** Pure rules shared by discovery, bounded downloads and JVM regression tests. */
internal object UpdatePolicy {
    const val CHECK_INTERVAL = 12 * 60 * 60 * 1000L
    const val MAX_ZIP = 150 * 1024 * 1024L
    const val MAX_APK = 100 * 1024 * 1024L
    private val stable = Regex("(0|[1-9][0-9]{0,7})\\.(0|[1-9][0-9]{0,7})\\.(0|[1-9][0-9]{0,7})")
    fun stableKey(value: String): List<Int>? = if (stable.matches(value)) value.split('.').map { it.toInt() } else null
    fun newerStable(target: String, current: String): Boolean {
        val a=stableKey(target) ?: return false
        val b=stableKey(current) ?: return false
        for (i in 0..2) if (a[i]!=b[i]) return a[i]>b[i]
        return false
    }
    fun allowedUrl(value: String): Boolean = try {
        val uri=URI(value)
        uri.scheme=="https" && uri.rawUserInfo==null && uri.port==-1 && uri.fragment==null &&
            uri.host in setOf("api.github.com","github.com","release-assets.githubusercontent.com","objects.githubusercontent.com")
    } catch (_: Exception) { false }
    fun shouldCheck(manual: Boolean, now: Long, last: Long): Boolean = manual || last==0L || now<last || now-last>=CHECK_INTERVAL
    fun shouldRemind(version: String, ignored: String, deferUntil: Long, now: Long): Boolean = version!=ignored && now>=deferUntil
    fun phaseAfterCheckFailure(manual: Boolean, ready: Boolean, candidate: Boolean):String =
        if(ready&&candidate)"ready" else if(manual)"error" else if(candidate)"available" else "idle"
    fun validHash(value: String): Boolean = Regex("[0-9a-f]{64}").matches(value)
    fun digest(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it.toInt() and 255) }
    fun copyVerified(input: InputStream, output: OutputStream, limit: Long, expectedHash: String?, expectedSize: Long?=null, progress: (Long)->Unit={}): Long {
        require(limit>0 && (expectedSize==null || expectedSize in 1..limit))
        require(expectedHash==null || validHash(expectedHash))
        val hash=MessageDigest.getInstance("SHA-256")
        val buffer=ByteArray(64*1024);var count=0L
        while (true) {
            val read=input.read(buffer);if(read<0)break;if(read==0)continue
            count+=read;require(count<=limit && (expectedSize==null || count<=expectedSize)) { "size" }
            hash.update(buffer,0,read);output.write(buffer,0,read);progress(count)
        }
        require(count>0 && (expectedSize==null || count==expectedSize)) { "size" }
        val actual=hash.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
        require(expectedHash==null || actual==expectedHash) { "hash" }
        return count
    }
}
