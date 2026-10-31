# Add project specific ProGuard rules here.
# You can control the set of applied configuration files using the
# proguardFiles setting in build.gradle.
#
# For more details, see
#   http://developer.android.com/guide/developing/tools/proguard.html

# If your project uses WebView with JS, uncomment the following
# and specify the fully qualified class name to the JavaScript interface
# class:
#-keepclassmembers class fqcn.of.javascript.interface.for.webview {
#   public *;
#}

# Uncomment this to preserve the line number information for
# debugging stack traces.
#-keepattributes SourceFile,LineNumberTable

# If you keep the line number information, uncomment this to
# hide the original source file name.
#-renamesourcefileattribute SourceFile

# rustls-platform-verifier: the Kotlin verifier component is only reached
# through JNI, which Proguard/R8 cannot see. Without this rule a release
# build strips it and every HTTPS handshake fails on Android.
-keep, includedescriptorclasses class org.rustls.platformverifier.** { *; }

# Display-only WebView interface is called by its JavaScript method name.
-keepclassmembers class com.account.preview.MainActivity$AppearanceBridge {
    @android.webkit.JavascriptInterface <methods>;
}
