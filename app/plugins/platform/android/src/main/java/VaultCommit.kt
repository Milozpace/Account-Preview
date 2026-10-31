package com.account.platform

/** Commit policy for an atomic-file API whose finish operation can fail silently.
 * Both buffers contain encrypted envelopes, never plaintext secrets.
 */
internal object VaultCommit {
    fun write(
        ciphertext: ByteArray,
        previous: ByteArray?,
        write: (ByteArray) -> Unit,
        read: () -> ByteArray?
    ) {
        try {
            write(ciphertext)
            // Read through AtomicFile so legacy backups/pending writes recover
            // exactly as on the next load. finishWrite returning is not success.
            check(read()?.contentEquals(ciphertext) == true)
        } catch (_: Exception) {
            if (previous != null) {
                try {
                    // A failed rename normally leaves the old record intact.
                    // Avoid replacing it again; restore only if it is missing,
                    // different or unreadable after the failed operation.
                    val retained = try { read()?.contentEquals(previous) == true } catch (_: Exception) { false }
                    if (!retained) {
                        write(previous)
                        check(read()?.contentEquals(previous) == true)
                    }
                } catch (_: Exception) {
                    // Recovery can also fail (e.g. inaccessible storage). Never
                    // turn either failure into success or disclose its details.
                }
            }
            throw IllegalStateException("vault storage operation failed")
        }
    }
}
