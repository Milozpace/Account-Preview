package com.account.platform

import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.IOException

class MarkdownExportTest {
    @Test fun reportWorkerHasOneThreadAndRejectsAnUnboundedBacklog() {
        val worker = MarkdownExport.newWorker()
        val started = java.util.concurrent.CountDownLatch(1)
        val release = java.util.concurrent.CountDownLatch(1)
        try {
            worker.execute { started.countDown(); release.await() }
            assertTrue(started.await(2, java.util.concurrent.TimeUnit.SECONDS))
            worker.execute { }
            assertThrows(java.util.concurrent.RejectedExecutionException::class.java) { worker.execute { } }
        } finally {
            release.countDown()
            worker.shutdownNow()
            assertTrue(worker.awaitTermination(2, java.util.concurrent.TimeUnit.SECONDS))
        }
    }
    @Test fun acceptsReportOnlyAndCountsUtf8Bytes() {
        assertArrayEquals("# 复盘\n".toByteArray(Charsets.UTF_8), MarkdownExport.validate("消费复盘_2026-10.md", "# 复盘\n"))
        assertEquals(MarkdownExport.MAX_BYTES, MarkdownExport.validate("消费复盘_2026-01.md", "a".repeat(MarkdownExport.MAX_BYTES)).size)
        assertThrows(IllegalArgumentException::class.java) { MarkdownExport.validate("消费复盘_2026-01.md", "中".repeat(MarkdownExport.MAX_BYTES / 3 + 1)) }
        for (name in listOf("../消费复盘_2026-10.md", "a.md", "消费复盘_2026-00.md", "消费复盘_2026-13.md", "消费复盘_0000-01.md", "消费复盘_2026-10.md.exe", "消费复盘_2026-10.md\n")) {
            assertThrows(IllegalArgumentException::class.java) { MarkdownExport.validate(name, "text") }
        }
    }

    @Test fun writesOnlyProvidedStreamAndClosesBeforeSuccess() {
        var closed = false
        val output = object : ByteArrayOutputStream() {
            override fun close() { closed = true; super.close() }
        }
        val content = "# 中文\n".toByteArray(Charsets.UTF_8)
        MarkdownExport.writeSelected(content) { output }
        assertArrayEquals(content, output.toByteArray())
        assertTrue(closed)
    }

    @Test fun unavailableOrFailingStreamNeverSucceeds() {
        assertThrows(IOException::class.java) { MarkdownExport.writeSelected(byteArrayOf(1)) { null } }
        assertThrows(IOException::class.java) {
            MarkdownExport.writeSelected(byteArrayOf(1)) { object : java.io.OutputStream() {
                override fun write(value: Int) { throw IOException("synthetic write failure") }
            } }
        }
    }
}
