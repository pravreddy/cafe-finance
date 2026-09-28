package `in`.co.guhya.cafefinance

import android.content.Intent
import android.os.Bundle
import com.getcapacitor.BridgeActivity

class MainActivity : BridgeActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        // Register our plugin BEFORE Capacitor boots the WebView.
        registerPlugin(ShareInPlugin::class.java)
        super.onCreate(savedInstanceState)
        ShareInPlugin.stash(intent)
    }

    // App already open and the user shares again: Android delivers a new intent
    // here instead of onCreate. Stash it and reload so the web page picks it up.
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (ShareInPlugin.stash(intent)) {
            bridge?.webView?.post { bridge.webView.loadUrl(bridge.appUrl) }
        }
    }
}
