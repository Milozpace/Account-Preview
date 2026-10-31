package com.account.platform

import org.junit.Assert.*
import org.junit.Test

class VaultFormatTest {
    @Test fun validatesStrictBoundaries() {
        for (slot in listOf("", "../secret", "a/b", "a\\b", "UPPER", "令牌", "a".repeat(65))) {
            assertThrows(IllegalArgumentException::class.java) { VaultFormat.validateSlot(slot) }
        }
        VaultFormat.validateSlot("a")
        VaultFormat.validateSlot("token-01")
        VaultFormat.validateSlot("a".repeat(64))
        VaultFormat.validateSecret(ByteArray(512 * 1024))
        assertThrows(IllegalArgumentException::class.java) { VaultFormat.validateSecret(ByteArray(512 * 1024 + 1)) }
    }
    @Test fun rejectsCorruptionAndBoundsEnvelopeBeforeDecrypting() {
        val nonce = ByteArray(12) { it.toByte() }
        val encrypted = ByteArray(32) { (it + 17).toByte() }
        val envelope = VaultFormat.encode(nonce, encrypted)
        val parts = VaultFormat.decode(envelope)
        assertArrayEquals(nonce, parts.first)
        assertArrayEquals(encrypted, parts.second)
        for (bad in listOf(ByteArray(0), envelope.copyOf(12), envelope.copyOf().also { it[0] = 0 }, ByteArray(512 * 1024 + 33))) {
            assertThrows(IllegalArgumentException::class.java) { VaultFormat.decode(bad) }
        }
    }
}
