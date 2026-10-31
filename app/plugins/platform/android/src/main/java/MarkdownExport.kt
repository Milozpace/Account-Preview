package com.account.platform

internal object MarkdownExport {
    fun newWorker(): java.util.concurrent.ExecutorService = java.util.concurrent.ThreadPoolExecutor(
        1, 1, 0L, java.util.concurrent.TimeUnit.MILLISECONDS,
        java.util.concurrent.ArrayBlockingQueue<Runnable>(1),
        { task -> Thread(task, "Account-report-save").apply { isDaemon = true } },
        java.util.concurrent.ThreadPoolExecutor.AbortPolicy()
    )
    const val MAX_BYTES = 4 * 1024 * 1024
    private val reportName = Regex("消费复盘_[0-9]{4}-(0[1-9]|1[0-2])\\.md")

    fun validate(fileName: String, content: String): ByteArray {
        require(reportName.matches(fileName) && !fileName.startsWith("消费复盘_0000-"))
        require(content.length <= MAX_BYTES)
        val bytes = content.toByteArray(Charsets.UTF_8)
        require(bytes.size <= MAX_BYTES)
        return bytes
    }

    /** Success is reported only after the selected provider stream closes. */
    fun writeSelected(content: ByteArray, open: () -> java.io.OutputStream?) {
        val output = open() ?: throw java.io.IOException("document stream unavailable")
        output.use { it.write(content); it.flush() }
    }
}
