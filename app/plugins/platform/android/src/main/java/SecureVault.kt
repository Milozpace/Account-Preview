package com.account.platform

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.io.FileNotFoundException
import java.io.FileOutputStream
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Native-only vault. Never log arguments, plaintext, or crypto exception details. */
internal class SecureVault(
    context: Context,
    directory: String = "account-vault",
    private val keyAlias: String = "com.account.platform.vault.aes256.v1",
    private val finishWrite: (AtomicFile, FileOutputStream) -> Unit = { file, stream -> file.finishWrite(stream) }
) {
    // Android excludes this directory from cloud backup and device-to-device transfer.
    private val root = File(context.noBackupFilesDir, directory)
    private val keyStore by lazy {
        KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    }

    private fun file(slot: String): AtomicFile {
        VaultFormat.validateSlot(slot)
        return AtomicFile(File(root, "$slot.bin"))
    }

    private fun existingKey(): SecretKey? {
        val key = keyStore.getKey(keyAlias, null) ?: return null
        return key as? SecretKey ?: error("vault encryption key is unavailable")
    }

    private fun encryptionKey(): SecretKey {
        existingKey()?.let { return it }
        // Key loss is an error. Never generate a replacement when encrypted data exists.
        val records = if (root.exists()) {
            check(root.isDirectory) { "vault storage operation failed" }
            root.listFiles() ?: error("vault storage operation failed")
        } else {
            emptyArray()
        }
        check(records.none { it.name.endsWith(".bin") || it.name.endsWith(".bin.bak") || it.name.endsWith(".bin.new") }) {
            "vault encryption key is unavailable"
        }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(keyAlias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setKeySize(256)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build()
        )
        return generator.generateKey()
    }

    @Synchronized fun store(slot: String, secret: ByteArray) {
        val destination = file(slot)
        VaultFormat.validateSecret(secret)
        // Detect corruption before replacing an existing record.
        load(slot)?.fill(0)
        val previous = readCiphertext(slot)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, encryptionKey())
        cipher.updateAAD(aad(slot))
        val ciphertext = VaultFormat.encode(cipher.iv, cipher.doFinal(secret))
        check(root.isDirectory || root.mkdirs()) { "vault storage operation failed" }
        VaultCommit.write(ciphertext, previous, { writeCiphertext(destination, it) }, {
            readCiphertext(slot).also {
                // openRead also only logs recovery failures. A legacy backup
                // left behind could roll this record back on a later load.
                check(!File(root, "$slot.bin.bak").exists() && !File(root, "$slot.bin.new").exists()) {
                    "vault storage operation failed"
                }
            }
        })
    }

    private fun writeCiphertext(destination: AtomicFile, ciphertext: ByteArray) {
        val stream = destination.startWrite()
        try {
            stream.write(ciphertext)
            stream.fd.sync()
            finishWrite(destination, stream)
        } catch (error: Exception) {
            destination.failWrite(stream)
            throw error
        }
    }

    @Synchronized fun load(slot: String): ByteArray? {
        val bytes = readCiphertext(slot) ?: return null
        val (nonce, ciphertext) = VaultFormat.decode(bytes)
        val key = existingKey() ?: error("vault encryption key is unavailable")
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, nonce))
        cipher.updateAAD(aad(slot))
        return cipher.doFinal(ciphertext).also { VaultFormat.validateSecret(it) }
    }

    private fun readCiphertext(slot: String): ByteArray? {
        val source = file(slot)
        val stream = try {
            source.openRead()
        } catch (error: FileNotFoundException) {
            // A damaged or unreadable file must never masquerade as a missing slot.
            if (!source.baseFile.exists() && !File(root, "$slot.bin.bak").exists() && !File(root, "$slot.bin.new").exists()) return null
            throw error
        }
        return stream.use {
            val buffer = ByteArray(VaultFormat.MAX_ENVELOPE_BYTES + 1)
            var length = 0
            while (length < buffer.size) {
                val count = it.read(buffer, length, buffer.size - length)
                if (count < 0) break
                check(count > 0) { "vault storage operation failed" }
                length += count
            }
            buffer.copyOf(length)
        }
    }

    @Synchronized fun delete(slot: String) {
        val destination = file(slot)
        destination.delete()
        check(!destination.baseFile.exists() && !File(root, "$slot.bin.bak").exists() && !File(root, "$slot.bin.new").exists()) {
            "vault storage operation failed"
        }
        // Keep the wrapping key; deleting one slot must not invalidate other slots.
    }

    private fun aad(slot: String): ByteArray = "com.account.app:vault:v1:$slot".toByteArray(Charsets.US_ASCII)
}
