package com.account.platform

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class UpdateSourceTest {
    private val prefix="https://raw.githubusercontent.com/Milozpace/Account-Preview/codex/update-download-test-20261114/"
    private fun marker()=JSONObject().put("format",1).put("updateTestOnly",true).put("branch","codex/update-download-test-20261114").put("productVersion","0.4.0").toString()
    @Test fun productionNeverUsesRawTestCatalogOrVersionOverride() {
        val source=UpdateSource.fromMarker(null)
        assertFalse(source.isTest);assertNull(source.productVersion)
        assertTrue(source.catalogUrl.startsWith("https://api.github.com/"))
        val index=source.staticCatalogUrl!!
        assertTrue(source.allows(index))
        assertFalse(source.assetAllowed(index))
        assertFalse(source.allows(index+"?other=1"))
        assertFalse(source.allows(index.replace("android-update-index","main")))
        assertFalse(source.allows(prefix+"releases.json"))
    }
    @Test fun signedTestMarkerConfinesAllFixturesToExactBranch() {
        val source=UpdateSource.fromMarker(marker())
        assertTrue(source.isTest);assertEquals("0.4.0",source.productVersion)
        assertEquals(prefix+"releases.json",source.catalogUrl)
        assertNull(source.staticCatalogUrl)
        assertFalse(source.allows(UpdateSource.PUBLIC_CATALOG))
        assertTrue(source.allows(prefix+"android.zip"));assertTrue(source.assetAllowed(prefix+"release-manifest.json"))
        for(url in listOf(prefix+"../other.zip",prefix+"%2e%2e/other.zip",prefix+"a.apk?x=1",prefix.replace("https:","http:")+"android.zip",prefix.replace("20261114","20261115")+"android.zip"))assertFalse(url,source.allows(url))
        assertFalse(source.assetAllowed("https://github.com/Milozpace/Account-Preview/releases/download/v0.4.1/a.zip"))
    }
    @Test fun invalidMarkerDoesNotSilentlyEnableAnArbitraryHost() {
        for(branch in listOf("main","codex/other","../main","codex/update-download-test-20261114/other")) {
            val marker=JSONObject(marker()).put("branch",branch)
            try {UpdateSource.fromMarker(marker.toString());fail("reject branch") }catch(_:IllegalArgumentException){}
        }
    }
}
