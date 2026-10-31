package com.account.platform

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.io.IOException
import java.nio.file.Files

class VaultCommitTest {
    // AtomicFile may finish normally while its rename fails. Model that void API
    // at the disk boundary; assertions inspect the actual persisted bytes.
    private fun withRecord(test: (File, ByteArray, ByteArray) -> Unit) {
        val root = Files.createTempDirectory("vault-commit-test").toFile()
        try {
            val record = File(root, "token.bin")
            val old = VaultFormat.encode(ByteArray(12) { 1 }, ByteArray(24) { 2 })
            val next = VaultFormat.encode(ByteArray(12) { 3 }, ByteArray(24) { 4 })
            record.writeBytes(old)
            test(record, old, next)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test fun rejectsSilentRenameFailureAndRetainsPriorRecord() = withRecord { record, old, next ->
        var writes = 0
        val failure = assertThrows(IllegalStateException::class.java) {
            VaultCommit.write(next, old, { writes++ /* silent rename failure */ }, { record.readBytes() })
        }
        assertEquals("vault storage operation failed", failure.message)
        assertNull(failure.cause)
        assertEquals(1, writes)
        assertArrayEquals(old, record.readBytes())
    }

    @Test fun verifiesSuccessfulReplacementAgainstDisk() = withRecord { record, old, next ->
        var reads = 0
        VaultCommit.write(next, old, { record.writeBytes(it) }, { reads++; record.readBytes() })
        assertEquals(1, reads)
        assertArrayEquals(next, record.readBytes())
    }

    @Test fun restoresPriorCiphertextWhenCommitReadbackIsCorrupt() = withRecord { record, old, next ->
        var writes = 0
        assertThrows(IllegalStateException::class.java) {
            VaultCommit.write(next, old, {
                writes++
                record.writeBytes(if (writes == 1) it.copyOf(10) else it)
            }, { record.readBytes() })
        }
        assertEquals(2, writes)
        assertArrayEquals(old, record.readBytes())
    }

    @Test fun restoresPriorCiphertextWhenReadbackThrows() = withRecord { record, old, next ->
        var reads = 0
        assertThrows(IllegalStateException::class.java) {
            VaultCommit.write(next, old, { record.writeBytes(it) }, {
                reads++
                if (reads == 1) throw IOException("synthetic private path or token")
                record.readBytes()
            })
        }
        assertArrayEquals(old, record.readBytes())
    }

    @Test fun firstWriteMustBeReadableAndEqualBeforeReturningSuccess() {
        val next = VaultFormat.encode(ByteArray(12), ByteArray(16))
        assertThrows(IllegalStateException::class.java) {
            VaultCommit.write(next, null, { /* silently uncommitted */ }, { null })
        }
    }

    @Test fun failedRecoveryDoesNotHideOriginalFailureOrExceptionDetails() = withRecord { record, old, next ->
        var writes = 0
        val failure = assertThrows(IllegalStateException::class.java) {
            VaultCommit.write(next, old, {
                writes++
                if (writes > 1) throw IOException("synthetic private path or token")
                record.writeBytes(it.copyOf(10))
            }, { record.readBytes() })
        }
        assertEquals(2, writes)
        assertEquals("vault storage operation failed", failure.message)
        assertNull(failure.cause)
        assertTrue(failure.suppressed.isEmpty())
    }
}
