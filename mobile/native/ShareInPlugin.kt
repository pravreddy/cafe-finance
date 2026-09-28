package `in`.cafegopala.ledger

import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Base64
import com.getcapacitor.JSArray
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin

/**
 * ShareIn — the ONLY native code in this app.
 *
 * Android opens us with ACTION_SEND / ACTION_SEND_MULTIPLE when the user picks
 * "Cafe Finance" in the share sheet (from PhonePe, GPay, WhatsApp, Gmail, the
 * gallery…). We keep the intent, and the web page (base.html) calls
 * `ShareIn.consume()` on load: it gets the files as base64 and POSTs them to
 * /share/ exactly like the PWA share_target does. One-shot: a second consume()
 * returns nothing, so a page reload never double-posts.
 */
@CapacitorPlugin(name = "ShareIn")
class ShareInPlugin : Plugin() {

    companion object {
        @Volatile private var pending: Intent? = null

        /** Remember a share intent; returns true if there was one. */
        fun stash(intent: Intent?): Boolean {
            val a = intent?.action ?: return false
            if (a == Intent.ACTION_SEND || a == Intent.ACTION_SEND_MULTIPLE) {
                pending = intent
                return true
            }
            return false
        }
    }

    @PluginMethod
    fun consume(call: PluginCall) {
        val intent = pending
        pending = null
        val out = JSObject()
        val files = JSArray()
        if (intent != null) {
            val uris = mutableListOf<Uri>()
            if (intent.action == Intent.ACTION_SEND) {
                @Suppress("DEPRECATION")
                (intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM))?.let { uris += it }
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)?.let { uris += it }
            }
            val cr = context.contentResolver
            for (uri in uris.take(10)) {
                try {
                    val bytes = cr.openInputStream(uri)?.use { it.readBytes() } ?: continue
                    if (bytes.size > 20 * 1024 * 1024) continue
                    val f = JSObject()
                    f.put("name", displayName(uri) ?: "shared")
                    f.put("mime", cr.getType(uri) ?: intent.type ?: "application/octet-stream")
                    f.put("data", Base64.encodeToString(bytes, Base64.NO_WRAP))
                    files.put(f)
                } catch (_: Exception) { }
            }
            out.put("text", intent.getStringExtra(Intent.EXTRA_TEXT) ?: "")
            // Heuristic only: WhatsApp's file provider carries its package name.
            out.put("fromWhatsApp", uris.any { it.toString().contains("whatsapp") })
        }
        out.put("files", files)
        call.resolve(out)
    }

    private fun displayName(uri: Uri): String? {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) return c.getString(0)
        }
        return uri.lastPathSegment
    }
}
