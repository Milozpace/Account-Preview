package com.account.platform

import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.security.KeyStore

@RunWith(AndroidJUnit4::class)
class SecureVaultInstrumentedTest {
    private fun vault(): SecureVault = SecureVault(
        InstrumentationRegistry.getInstrumentation().targetContext,
        "vault-instrumented-tests",
        "com.account.platform.vault.instrumented-test.v1"
    )

    @Test fun roundtripCiphertextOnlyAndUniqueNonce() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val vault = vault()
        val secret = "native-test-only-never-real-token".toByteArray()
        vault.delete("roundtrip")
        assertNull(vault.load("roundtrip"))
        vault.store("roundtrip", secret)
        assertArrayEquals(secret, vault.load("roundtrip"))
        val file = File(context.noBackupFilesDir, "vault-instrumented-tests/roundtrip.bin")
        val first = file.readBytes()
        assertFalse(String(first, Charsets.ISO_8859_1).contains(String(secret)))
        vault.store("roundtrip", secret)
        assertFalse(first.contentEquals(file.readBytes()))
        vault.delete("roundtrip")
        vault.delete("roundtrip")
        assertNull(vault.load("roundtrip"))
    }

    @Test fun tamperingAndSlotSwapFailWithoutReset() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val vault = vault()
        vault.delete("first")
        vault.delete("second")
        vault.store("first", byteArrayOf(1, 2, 3))
        val first = File(context.noBackupFilesDir, "vault-instrumented-tests/first.bin")
        val second = File(context.noBackupFilesDir, "vault-instrumented-tests/second.bin")
        second.writeBytes(first.readBytes())
        assertThrows(Exception::class.java) { vault.load("second") }
        val corrupted = first.readBytes().also { it[it.lastIndex] = (it.last().toInt() xor 128).toByte() }
        first.writeBytes(corrupted)
        assertThrows(Exception::class.java) { vault.load("first") }
        assertThrows(Exception::class.java) { vault.store("first", byteArrayOf(4)) }
        assertArrayEquals(corrupted, first.readBytes())
        vault.delete("first")
        vault.delete("second")
    }

    @Test fun invalidInputCannotEscapePrivateVault() {
        val vault = vault()
        for (slot in listOf("", "../secret", "a/b", "a\\b", "UPPER", "令牌", "a".repeat(65))) {
            assertThrows(IllegalArgumentException::class.java) { vault.store(slot, byteArrayOf(1)) }
            assertThrows(IllegalArgumentException::class.java) { vault.load(slot) }
            assertThrows(IllegalArgumentException::class.java) { vault.delete(slot) }
        }
        assertThrows(IllegalArgumentException::class.java) { vault.store("oversized", ByteArray(512 * 1024 + 1)) }
    }

    @Test fun silentAtomicCommitFailureIsAnErrorAndRecoversTheOldSecret() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val directory = "vault-instrumented-commit-failure"
        val alias = "com.account.platform.vault.instrumented-commit-failure.v1"
        val vault = SecureVault(context, directory, alias)
        val failedCommitVault = SecureVault(context, directory, alias) { _, stream ->
            // Like AOSP's logged rename failure: close the synced .new stream,
            // return normally, leave the previous base record in place.
            stream.close()
        }
        val old = byteArrayOf(10, 20, 30)
        vault.delete("existing")
        vault.delete("first-write")
        try {
            vault.store("existing", old)
            val record = File(context.noBackupFilesDir, "$directory/existing.bin")
            val previousCiphertext = record.readBytes()
            val failure = assertThrows(IllegalStateException::class.java) {
                failedCommitVault.store("existing", byteArrayOf(40, 50))
            }
            assertEquals("vault storage operation failed", failure.message)
            assertNull(failure.cause)
            assertArrayEquals(previousCiphertext, record.readBytes())
            assertArrayEquals(old, SecureVault(context, directory, alias).load("existing"))
            assertFalse(File(context.noBackupFilesDir, "$directory/existing.bin.new").exists())
            assertThrows(IllegalStateException::class.java) {
                failedCommitVault.store("first-write", byteArrayOf(60))
            }
            // An uncommitted first write must remain an explicit read failure;
            // it must never be mistaken for an empty/missing usable record.
            assertThrows(Exception::class.java) { vault.load("first-write") }
        } finally {
            vault.delete("existing")
            vault.delete("first-write")
        }
    }

    @Test fun keyLossFailsAndCannotSilentlyRecreateAKeyForAnotherSlot() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val alias = "com.account.platform.vault.instrumented-keyloss.v1"
        val vault = SecureVault(context, "vault-instrumented-keyloss", alias)
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        vault.delete("existing")
        vault.delete("new-slot")
        keyStore.deleteEntry(alias)
        vault.store("existing", byteArrayOf(10, 20, 30))
        val file = File(context.noBackupFilesDir, "vault-instrumented-keyloss/existing.bin")
        val ciphertext = file.readBytes()
        keyStore.deleteEntry(alias)
        assertThrows(Exception::class.java) { vault.load("existing") }
        assertThrows(Exception::class.java) { vault.store("existing", byteArrayOf(40)) }
        assertThrows(Exception::class.java) { vault.store("new-slot", byteArrayOf(50)) }
        assertFalse(keyStore.containsAlias(alias))
        assertArrayEquals(ciphertext, file.readBytes())
        vault.delete("existing")
        vault.delete("new-slot")
    }
}
