package com.account.preview

import android.os.Bundle
import android.os.Build
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.webkit.JavascriptInterface
import android.webkit.WebSettings
import android.content.res.Configuration
import android.webkit.WebView
import androidx.activity.enableEdgeToEdge
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import android.view.ViewGroup
import kotlin.math.max
import kotlin.math.roundToInt

class MainActivity : TauriActivity() {
  // Wry maps the system back gesture to WebView history. Frontend entries
  // restore the originating tab, filters and scroll position on popstate.
  override val handleBackNavigation: Boolean = true
  private var ledgerWebView: WebView? = null
  private val appearancePreferences by lazy { getSharedPreferences("appearance", MODE_PRIVATE) }
  private var activeAppearance: String? = null
  // Accessed by the WebView JavaScript interface thread only.
  private var appearanceNeedsCommit = false

  // This interface can change display preferences only. It has no file, account or sync APIs.
  inner class AppearanceBridge {
    @JavascriptInterface
    fun getSystemTheme(): String {
      val dark = resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK == Configuration.UI_MODE_NIGHT_YES
      return if (dark) "dark" else "light"
    }

    @JavascriptInterface
    fun getAppearancePreference(): String {
      val value = appearancePreferences.getString("theme", "system")
      return if (value == "light" || value == "dark") value else "system"
    }

    @JavascriptInterface
    fun setAppearance(value: String): Boolean {
      if (value != "system" && value != "light" && value != "dark") return false
      // JavaScriptInterface runs off the UI thread. Commit the tiny display value
      // before returning, so a process restart cannot lose a confirmed selection.
      val saved = if (appearanceNeedsCommit || appearancePreferences.getString("theme", "system") != value) {
        appearancePreferences.edit().putString("theme", value).commit()
      } else true
      appearanceNeedsCommit = !saved
      runOnUiThread {
        if (isDestroyed) return@runOnUiThread
        activeAppearance = value
        applyAppearance()
      }
      return saved
    }
  }

  private fun applyAppearance() {
    val preference = activeAppearance ?: appearancePreferences.getString("theme", "system")
    val systemDark = resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK == Configuration.UI_MODE_NIGHT_YES
    val dark = preference == "dark" || (preference != "light" && systemDark)
    val background = Color.parseColor(if (dark) "#161719" else "#f5f4f0")
    window.setBackgroundDrawable(ColorDrawable(background))
    window.decorView.setBackgroundColor(background)
    ledgerWebView?.setBackgroundColor(background)
    WindowInsetsControllerCompat(window, ledgerWebView ?: window.decorView).apply {
      isAppearanceLightStatusBars = !dark
      isAppearanceLightNavigationBars = !dark
    }
  }
  override fun onCreate(savedInstanceState: Bundle?) {
    enableEdgeToEdge()
    super.onCreate(savedInstanceState)
    applyAppearance()
  }

  override fun onWebViewCreate(webView: WebView) {
    super.onWebViewCreate(webView)
    ledgerWebView = webView
    webView.settings.textZoom = (resources.configuration.fontScale * 100).roundToInt()
    webView.addJavascriptInterface(AppearanceBridge(), "AccountAppearance")
    if (Build.VERSION.SDK_INT >= 33) webView.settings.isAlgorithmicDarkeningAllowed = false
    else if (Build.VERSION.SDK_INT >= 29) {
      @Suppress("DEPRECATION")
      webView.settings.forceDark = WebSettings.FORCE_DARK_OFF
    }
    applyAppearance()
    // Android 16 enforces edge-to-edge. Reserve system/IME space natively;
    // CSS safe-area variables remain available for platforms that expose them.
    ViewCompat.setOnApplyWindowInsetsListener(webView) { view, insets ->
      val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
      val keyboard = insets.getInsets(WindowInsetsCompat.Type.ime())
      (view.layoutParams as? ViewGroup.MarginLayoutParams)?.let { layout ->
        val bottom = max(bars.bottom, keyboard.bottom)
        if (layout.leftMargin != bars.left || layout.topMargin != bars.top || layout.rightMargin != bars.right || layout.bottomMargin != bottom) {
          layout.setMargins(bars.left, bars.top, bars.right, bottom)
          view.layoutParams = layout
        }
      }
      insets
    }
    ViewCompat.requestApplyInsets(webView)
  }

  override fun onConfigurationChanged(newConfig: Configuration) {
    super.onConfigurationChanged(newConfig)
    ledgerWebView?.settings?.textZoom = (newConfig.fontScale * 100).roundToInt()
    applyAppearance()
    // Some WebViews keep prefers-color-scheme light after an Activity uiMode change.
    // Notify the existing page; do not reload forms, filters or cached ledger data.
    ledgerWebView?.evaluateJavascript("window.dispatchEvent(new Event('account-system-appearance'))", null)
  }
}
