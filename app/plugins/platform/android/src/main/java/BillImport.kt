package com.account.platform

/** Native-only SAF input helpers. No file paths or document URIs are accepted from JS. */
internal object BillImport {
    const val MAX_BYTES = 4 * 1024 * 1024

    fun <T> selectItems(items: List<T>): List<T> {
        require(items.isNotEmpty() && items.size <= 128)
        return items.distinct()
    }

    fun validateName(name: String): String {
        require(name.isNotEmpty() && name.toByteArray(Charsets.UTF_8).size <= 200 && name == name.trim())
        require(name.none { it.isISOControl() || it in "/\\:<>\"|?*" })
        require(!name.endsWith(".") && !name.contains(".."))
        val extension = name.substringAfterLast('.', "").lowercase(java.util.Locale.ROOT)
        require(extension == "csv" || extension == "xlsx")
        val stem = name.substringBefore('.').uppercase(java.util.Locale.ROOT)
        require(stem !in setOf("CON", "PRN", "AUX", "NUL") && !Regex("(COM|LPT)[1-9]").matches(stem))
        return name
    }

    /** Read at most MAX_BYTES + 1, even when provider size metadata lies or is absent. */
    fun readSelected(fileName: String, reportedSize: Long?, open: () -> java.io.InputStream?): ByteArray {
        validateName(fileName)
        require(reportedSize == null || reportedSize <= MAX_BYTES)
        val scratch = ByteArray(MAX_BYTES + 1)
        try {
            val input = open() ?: throw java.io.IOException("document unavailable")
            val count = input.use {
                var used = 0
                while (used <= MAX_BYTES) {
                    val read = it.read(scratch, used, scratch.size - used)
                    if (read < 0) break
                    if (read == 0) {
                        val single = it.read()
                        if (single < 0) break
                        scratch[used++] = single.toByte()
                    } else {
                        used += read
                    }
                }
                require(used in 1..MAX_BYTES)
                used
            }
            return scratch.copyOf(count)
        } finally {
            scratch.fill(0)
        }
    }

    fun newWorker(): java.util.concurrent.ExecutorService = java.util.concurrent.ThreadPoolExecutor(
        1, 1, 0L, java.util.concurrent.TimeUnit.MILLISECONDS,
        java.util.concurrent.ArrayBlockingQueue<Runnable>(1),
        { task -> Thread(task, "Account-bill-select").apply { isDaemon = true } },
        java.util.concurrent.ThreadPoolExecutor.AbortPolicy()
    )

    /** Identity-bound phases prevent a late callback from releasing a newer selection. */
    class SelectionGate {
        private var current: Any? = null
        private var resultClaimed = false
        @Synchronized fun begin(token: Any): Boolean {
            if (current != null) return false
            current = token
            resultClaimed = false
            return true
        }
        @Synchronized fun claimResult(token: Any): Boolean {
            if (current !== token || resultClaimed) return false
            resultClaimed = true
            return true
        }
        @Synchronized fun finish(token: Any) {
            if (current === token) { current = null; resultClaimed = false }
        }
    }
}
