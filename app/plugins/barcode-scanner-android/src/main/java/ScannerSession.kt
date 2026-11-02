package app.tauri.barcodescanner

/** Main-thread ownership of one native scan; stale camera/ML Kit callbacks lose access. */
internal class ScannerSession<T> {
    var generation: Long = 0
        private set
    var pending: T? = null
        private set

    fun begin(invoke: T): Long {
        generation += 1
        pending = invoke
        return generation
    }

    fun isCurrent(token: Long): Boolean = pending != null && generation == token

    fun invalidate(): T? {
        val previous = pending
        generation += 1
        pending = null
        return previous
    }
}
