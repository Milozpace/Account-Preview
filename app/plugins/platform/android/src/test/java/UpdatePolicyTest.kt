package com.account.platform

import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream

class UpdatePolicyTest {
    @Test fun stableVersionsCompareNumericallyAndRejectPreview() {
        assertTrue(UpdatePolicy.newerStable("0.4.10", "0.4.9"))
        assertFalse(UpdatePolicy.newerStable("0.4.0", "0.4.0"))
        assertFalse(UpdatePolicy.newerStable("0.5.0-rc.1", "0.4.0"))
        assertFalse(UpdatePolicy.newerStable("01.4.1", "0.4.0"))
        assertFalse(UpdatePolicy.newerStable("0.4.1/evil", "0.4.0"))
    }
    @Test fun transportRejectsCleartextCredentialsPortsAndLookalikeHosts() {
        assertTrue(UpdatePolicy.allowedUrl("https://api.github.com/repos/Milozpace/Account-Preview/releases"))
        assertTrue(UpdatePolicy.allowedUrl("https://release-assets.githubusercontent.com/a?token=opaque"))
        for (url in listOf("http://github.com/a", "https://github.com.evil.test/a", "https://user:pass@github.com/a", "https://github.com:8443/a", "file:///tmp/a", "https://evil.test/github.com")) {
            assertFalse(url, UpdatePolicy.allowedUrl(url))
        }
    }
    @Test fun automaticChecksThrottleFailuresAndHandleClockRollback() {
        val now=100_000_000L
        assertFalse(UpdatePolicy.shouldCheck(false, now, now-1000))
        assertTrue(UpdatePolicy.shouldCheck(true, now, now-1000))
        assertTrue(UpdatePolicy.shouldCheck(false, now, now-12*60*60*1000L))
        assertTrue(UpdatePolicy.shouldCheck(false, now, now+1000))
    }
    @Test fun ignoredAndDeferredVersionsOnlySuppressAutomaticReminder() {
        assertFalse(UpdatePolicy.shouldRemind("0.4.1", "0.4.1", 0, 100))
        assertFalse(UpdatePolicy.shouldRemind("0.4.1", "", 200, 100))
        assertTrue(UpdatePolicy.shouldRemind("0.4.2", "0.4.1", 0, 100))
    }
    @Test fun failedRecheckPreservesReadyPackageAndUserCanStillInstall() {
        assertEquals("ready",UpdatePolicy.phaseAfterCheckFailure(true,true,true))
        assertEquals("ready",UpdatePolicy.phaseAfterCheckFailure(false,true,true))
        assertEquals("error",UpdatePolicy.phaseAfterCheckFailure(true,false,true))
        assertEquals("available",UpdatePolicy.phaseAfterCheckFailure(false,false,true))
        assertEquals("idle",UpdatePolicy.phaseAfterCheckFailure(false,false,false))
        assertEquals("idle",UpdatePolicy.phaseAfterCheckFailure(false,true,false))
    }
    @Test fun boundedCopyVerifiesDigestAndRejectsOverflowAndTruncation() {
        val bytes="abc".toByteArray();val hash="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        val output=ByteArrayOutputStream()
        assertEquals(3L, UpdatePolicy.copyVerified(ByteArrayInputStream(bytes),output,3,hash,3))
        assertArrayEquals(bytes, output.toByteArray())
        for ((limit,expected) in listOf(2L to hash,3L to "0".repeat(64))) {
            try { UpdatePolicy.copyVerified(ByteArrayInputStream(bytes),ByteArrayOutputStream(),limit,expected,3);fail("must reject") } catch (_: IllegalArgumentException) {}
        }
        try { UpdatePolicy.copyVerified(ByteArrayInputStream(bytes),ByteArrayOutputStream(),4,hash,4);fail("truncated") } catch (_: IllegalArgumentException) {}
    }
}
