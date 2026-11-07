package com.account.platform

import android.app.Activity
import android.content.Intent
import android.provider.OpenableColumns
import android.os.Handler
import android.os.Looper
import android.util.Base64
import androidx.activity.result.ActivityResult
import app.tauri.annotation.ActivityCallback
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import app.tauri.plugin.Invoke
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

@InvokeArg
class SlotArgs { var slot: String = "" }

@InvokeArg
class BillIndexArgs { var index: Int = -1 }

@InvokeArg
class StoreSecretArgs {
    var slot: String = ""
    var secret: String = ""
}

@InvokeArg
class SaveMarkdownArgs {
    var fileName: String = ""
    var content: String = ""
}

/** Internal PluginHandle bridge. No Rust invoke handler or JavaScript permissions. */
@TauriPlugin
class PlatformPlugin(activity: Activity): Plugin(activity) {
    private val reportActivity = activity
    private val vault = SecureVault(activity.applicationContext)
    private val worker = Executors.newSingleThreadExecutor()
    // Provider I/O must never queue ahead of vault/sync credential operations.
    private val reportWorker = MarkdownExport.newWorker()
    private val exportActive = AtomicBoolean(false)
    @Volatile private var exportBytes: ByteArray? = null
    // A provider may block; its work must never queue ahead of vault operations.
    private val billWorker = BillImport.newWorker()
    private val billSelection = BillImport.SelectionGate()
    @Volatile private var selectedBills: List<android.net.Uri> = emptyList()

    /** Native Rust bridge only. A provider picker grants access to one chosen document. */
    @Command
    fun selectBill(invoke: Invoke) {
        if (!billSelection.begin(invoke)) {
            invoke.reject("bill chooser already active")
            return
        }
        try {
            val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                addCategory(Intent.CATEGORY_OPENABLE)
                // Providers label exported CSV/XLSX inconsistently. Allow the native
                // chooser to show documents, then enforce the display-name suffix.
                type = "*/*"
                putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
            }
            reportActivity.runOnUiThread {
                try {
                    startActivityForResult(invoke, intent, "selectBillResult")
                } catch (_: Exception) {
                    billSelection.finish(invoke)
                    invoke.reject("bill chooser failed")
                }
            }
        } catch (_: Exception) {
            billSelection.finish(invoke)
            invoke.reject("bill chooser failed")
        }
    }

    @ActivityCallback
    private fun selectBillResult(invoke: Invoke, result: ActivityResult) {
        if (!billSelection.claimResult(invoke)) return
        try {
            if (result.resultCode == Activity.RESULT_CANCELED) {
                selectedBills = emptyList()
                invoke.resolve(JSObject().put("cancelled", true).put("count", 0))
            } else {
                require(result.resultCode == Activity.RESULT_OK)
                val data = result.data ?: error("document missing")
                val clip = data.clipData
                val uris = if (clip != null) (0 until clip.itemCount).map { clip.getItemAt(it).uri } else listOf(data.data ?: error("document missing"))
                require(uris.all { it.scheme == "content" })
                selectedBills = BillImport.selectItems(uris)
                invoke.resolve(JSObject().put("cancelled", false).put("count", selectedBills.size))
            }
        } catch (_: Exception) {
            selectedBills = emptyList()
            invoke.reject("bill selection failed")
        } finally {
            billSelection.finish(invoke)
        }
    }

    /** Native callers consume one selected document at a time; URIs never enter the WebView. */
    @Command
    fun readSelectedBill(invoke: Invoke) {
        try {
            val index = invoke.parseArgs(BillIndexArgs::class.java).index
            val uri = selectedBills.getOrNull(index) ?: error("document unavailable")
            val gate = BillImport.CompletionGate()
            val handler = Handler(Looper.getMainLooper())
            val timeout = Runnable {
                if (gate.claim()) invoke.resolve(JSObject().put("cancelled", false).put("failed", true).put("fileName", "文件 ${index + 1}"))
            }
            handler.postDelayed(timeout, 15_000L)
            try { billWorker.execute {
                var bytes: ByteArray? = null
                var displayName = "文件 ${index + 1}"
                try {
                    var name: String? = null
                    var size: Long? = null
                    reportActivity.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use {
                        require(it.moveToFirst())
                        val nameColumn = it.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                        require(nameColumn >= 0 && !it.isNull(nameColumn))
                        val rawName = it.getString(nameColumn)
                        displayName = rawName.filter { char -> !char.isISOControl() && char != '/' && char != '\\' }.take(200).ifBlank { displayName }
                        name = BillImport.validateName(rawName)
                        val sizeColumn = it.getColumnIndex(OpenableColumns.SIZE)
                        if (sizeColumn >= 0 && !it.isNull(sizeColumn)) size = it.getLong(sizeColumn)
                    }
                    val fileName = name ?: error("document name unavailable")
                    bytes = BillImport.readSelected(fileName, size) { reportActivity.contentResolver.openInputStream(uri) }
                    if (gate.claim()) {
                        handler.removeCallbacks(timeout)
                        invoke.resolve(JSObject().put("cancelled", false).put("fileName", fileName)
                            .put("contentBase64", Base64.encodeToString(bytes, Base64.NO_WRAP)))
                    }
                } catch (_: Exception) {
                    if (gate.claim()) {
                        handler.removeCallbacks(timeout)
                        invoke.resolve(JSObject().put("cancelled", false).put("failed", true).put("fileName", displayName))
                    }
                } finally { bytes?.fill(0) }
            } } catch (_: java.util.concurrent.RejectedExecutionException) {
                if (gate.claim()) {
                    handler.removeCallbacks(timeout)
                    invoke.resolve(JSObject().put("cancelled", false).put("failed", true).put("fileName", "文件 ${index + 1}"))
                }
            }
        } catch (_: Exception) { invoke.reject("bill selection failed") }
    }

    @Command
    fun clearSelectedBills(invoke: Invoke) {
        selectedBills = emptyList()
        invoke.resolve(JSObject())
    }

    /** Reached only by the Rust report command; the raw plugin rejects WebView calls. */
    @Command
    fun saveMarkdownReport(invoke: Invoke) {
        if (!exportActive.compareAndSet(false, true)) {
            invoke.reject("report save already active")
            return
        }
        reportWorker.execute {
            try {
                val args = invoke.parseArgs(SaveMarkdownArgs::class.java)
                exportBytes = MarkdownExport.validate(args.fileName, args.content)
                val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE)
                    type = "text/markdown"
                    putExtra(Intent.EXTRA_TITLE, args.fileName)
                }
                reportActivity.runOnUiThread {
                    try {
                        startActivityForResult(invoke, intent, "saveMarkdownResult")
                    } catch (_: Exception) {
                        finishExport()
                        invoke.reject("report chooser failed")
                    }
                }
            } catch (_: Exception) {
                finishExport()
                invoke.reject("report save failed")
            }
        }
    }

    @ActivityCallback
    private fun saveMarkdownResult(invoke: Invoke, result: ActivityResult) {
        reportWorker.execute {
            try {
                if (result.resultCode == Activity.RESULT_CANCELED) {
                    invoke.resolve(JSObject().put("saved", false))
                } else {
                    require(result.resultCode == Activity.RESULT_OK)
                    val uri = result.data?.data ?: error("document missing")
                    require(uri.scheme == "content")
                    val bytes = exportBytes ?: error("report missing")
                    MarkdownExport.writeSelected(bytes) {
                        reportActivity.contentResolver.openOutputStream(uri, "wt")
                    }
                    invoke.resolve(JSObject().put("saved", true))
                }
            } catch (_: Exception) {
                invoke.reject("report save failed")
            } finally {
                finishExport()
            }
        }
    }

    private fun finishExport() {
        exportBytes?.fill(0)
        exportBytes = null
        exportActive.set(false)
    }

    @Command
    fun storeSecret(invoke: Invoke) {
        worker.execute {
            var secret: ByteArray? = null
            try {
                val args = invoke.parseArgs(StoreSecretArgs::class.java)
                VaultFormat.validateSlot(args.slot)
                require(args.secret.length <= ((VaultFormat.MAX_SECRET_BYTES + 2) / 3) * 4)
                secret = Base64.decode(args.secret, Base64.NO_WRAP)
                vault.store(args.slot, secret)
                invoke.resolve(JSObject())
            } catch (_: Exception) {
                invoke.reject("native vault store failed")
            } finally {
                secret?.fill(0)
            }
        }
    }

    @Command
    fun loadSecret(invoke: Invoke) {
        worker.execute {
            var secret: ByteArray? = null
            try {
                val args = invoke.parseArgs(SlotArgs::class.java)
                secret = vault.load(args.slot)
                val response = JSObject()
                // JSON null is distinct from an empty base64 string (an empty secret).
                response.put("secret", secret?.let { Base64.encodeToString(it, Base64.NO_WRAP) } ?: org.json.JSONObject.NULL)
                invoke.resolve(response)
            } catch (_: Exception) {
                invoke.reject("native vault load failed")
            } finally {
                secret?.fill(0)
            }
        }
    }

    @Command
    fun deleteSecret(invoke: Invoke) {
        worker.execute {
            try {
                val args = invoke.parseArgs(SlotArgs::class.java)
                vault.delete(args.slot)
                invoke.resolve(JSObject())
            } catch (_: Exception) {
                invoke.reject("native vault delete failed")
            }
        }
    }
}
