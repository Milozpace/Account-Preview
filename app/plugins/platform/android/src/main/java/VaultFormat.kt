package com.account.platform

internal object VaultFormat {
    const val MAX_SECRET_BYTES = 512 * 1024
    private val magic = byteArrayOf(0x41, 0x56, 0x31, 0x01)
    const val MAX_ENVELOPE_BYTES = MAX_SECRET_BYTES + 32

    fun validateSlot(slot: String) {
        require(slot.length in 1..64 && slot.all { it in 'a'..'z' || it in '0'..'9' || it == '-' }) { "invalid vault slot" }
    }
    fun validateSecret(secret: ByteArray) {
        require(secret.size <= MAX_SECRET_BYTES) { "secret exceeds the vault size limit" }
    }
    fun encode(nonce: ByteArray, ciphertext: ByteArray): ByteArray {
        require(nonce.size == 12 && ciphertext.size in 16..MAX_SECRET_BYTES + 16) { "vault encryption failed" }
        return magic + nonce + ciphertext
    }
    fun decode(bytes: ByteArray): Pair<ByteArray, ByteArray> {
        require(bytes.size in 32..MAX_ENVELOPE_BYTES && bytes.copyOfRange(0, 4).contentEquals(magic)) { "vault secret cannot be decrypted" }
        return Pair(bytes.copyOfRange(4, 16), bytes.copyOfRange(16, bytes.size))
    }
}
