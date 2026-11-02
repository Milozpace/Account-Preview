package app.tauri.barcodescanner

import org.junit.Assert.*
import org.junit.Test

class ScannerSessionTest {
    @Test fun cancelledFrameCannotCompleteTheNextScan() {
        val session = ScannerSession<String>()
        val firstFrame = session.begin("old invoke")
        assertEquals("old invoke", session.invalidate())
        val nextFrame = session.begin("new invoke")
        val delivered = mutableListOf<String>()
        if (session.isCurrent(firstFrame)) delivered.add(session.pending!!)
        assertTrue(delivered.isEmpty())
        assertTrue(session.isCurrent(nextFrame))
        assertEquals("new invoke", session.pending)
    }

    @Test fun cancellingReturnsTheInvokeBeforeClearingIt() {
        val session = ScannerSession<String>()
        val token = session.begin("pending")
        val cancelled = session.invalidate()
        assertEquals("pending", cancelled)
        assertNull(session.pending)
        assertFalse(session.isCurrent(token))
        assertNull(session.invalidate())
    }

    @Test fun completedOrCancelledScanCannotBeReopenedByDelayedCameraSetup() {
        val session = ScannerSession<String>()
        val setupToken = session.begin("pending")
        session.invalidate()
        assertFalse(session.isCurrent(setupToken))
    }
}
