package com.nicobrailo.nannygodmin

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.edit
import androidx.core.net.toUri
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

class ConfigActivity : AppCompatActivity() {

    data class ProvisioningSettings(
        val serverUrl: String,
        val clientId: String,
        val pollIntervalSecs: Int
    )

    companion object {
        const val EXTRA_FORCE_REPROVISION = "EXTRA_FORCE_REPROVISION"
        private const val TAG = "ConfigActivity"
        private const val PREFS_NAME = "prefs"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_CLIENT_ID = "client_id"
        private const val KEY_POLL_INTERVAL = "poll_interval_secs"

        fun getSettings(context: Context): ProvisioningSettings? {
            val prefs = context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            val url = prefs.getString(KEY_SERVER_URL, null)
            val id = prefs.getString(KEY_CLIENT_ID, null)
            val interval = prefs.getInt(KEY_POLL_INTERVAL, 10)
            return if (url != null && id != null) {
                ProvisioningSettings(url, id, interval)
            } else {
                null
            }
        }

        fun clearClientId(context: Context) {
            context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit(commit = true) {
                remove(KEY_CLIENT_ID)
            }
        }
    }

    private lateinit var statusWarning: TextView
    private lateinit var urlStatus: TextView
    private lateinit var clientIdStatus: TextView
    private lateinit var urlInput: EditText
    private lateinit var btnSaveUrl: Button
    private lateinit var btnUnprovision: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(50, 50, 50, 50)
        }

        // Handle forced re-provisioning request from MainService
        val forceReprovision = intent.getBooleanExtra(EXTRA_FORCE_REPROVISION, false)
        if (forceReprovision) {
            Log.i(TAG, "Forced re-provisioning requested, clearing client_id")
            ConfigActivity.clearClientId(this)
        }

        val settings = ConfigActivity.getSettings(this)
        val currentUrl = settings?.serverUrl ?: getSharedPreferences(PREFS_NAME, MODE_PRIVATE).getString(KEY_SERVER_URL, "") ?: ""
        val currentClientId = settings?.clientId ?: ""

        statusWarning = TextView(this).apply {
            textSize = 20f
            setPadding(0, 0, 0, 20)
        }

        urlStatus = TextView(this).apply {
            setPadding(0, 0, 0, 10)
        }

        clientIdStatus = TextView(this).apply {
            setPadding(0, 0, 0, 20)
        }

        urlInput = EditText(this).apply {
            setHint(R.string.godmin_url_set_hint)
            setText(currentUrl)
        }

        btnSaveUrl = Button(this).apply {
            text = getString(R.string.save_url)
        }

        btnUnprovision = Button(this).apply {
            text = getString(R.string.unprovision)
            setOnClickListener {
                unprovisionDevice()
            }
        }

        setupPermissionButtons(layout)
        layout.addView(statusWarning)
        layout.addView(urlStatus)
        layout.addView(clientIdStatus)
        layout.addView(urlInput)
        layout.addView(btnSaveUrl)
        layout.addView(btnUnprovision)

        // Initial UI state setup
        updateUI(currentUrl, currentClientId)

        btnSaveUrl.setOnClickListener {
            val newUrl = urlInput.text.toString().trim()
            if (newUrl.isNotEmpty()) {
                btnSaveUrl.isEnabled = false // Gray out immediately
                provisionDevice(newUrl)
            } else {
                Toast.makeText(this@ConfigActivity, R.string.please_enter_url, Toast.LENGTH_SHORT).show()
            }
        }

        // Handle Deep Link
        intent?.data?.let { uri ->
            if (uri.scheme == "nannygodmin" && uri.host == "config") {
                val newUrl = uri.getQueryParameter("url")
                if (newUrl != null) {
                    btnSaveUrl.isEnabled = false
                    provisionDevice(newUrl)
                }
            }
        }

        setContentView(layout)

        // Start the service if device is provisioned and we're not forced to re-provision
        if (settings != null && !forceReprovision) {
            Log.i(TAG, "Device already provisioned, starting MainService")
            startForegroundService(Intent(this, MainService::class.java))
        }
    }

    private fun setupPermissionButtons(layout: LinearLayout) {
        val dpm = getSystemService(DEVICE_POLICY_SERVICE) as DevicePolicyManager
        val adminName = ComponentName(this, AdminReceiver::class.java)

        val btnEnableAdmin = Button(this).apply {
            text = getString(R.string.enable_device_admin)
            setOnClickListener {
                if (dpm.isAdminActive(adminName)) {
                    Toast.makeText(this@ConfigActivity, R.string.admin_already_active, Toast.LENGTH_SHORT).show()
                } else {
                    val intent = Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN).apply {
                        putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, adminName)
                        putExtra(DevicePolicyManager.EXTRA_ADD_EXPLANATION, getString(R.string.admin_explanation))
                    }
                    startActivity(intent)
                }
            }
        }

        val btnUsageStats = Button(this).apply {
            text = getString(R.string.enable_usage_stats)
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
            }
        }

        val btnOverlay = Button(this).apply {
            text = getString(R.string.enable_overlay_permission)
            setOnClickListener {
                val intent = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    "package:$packageName".toUri())
                startActivity(intent)
            }
        }

        val btnAccessibility = Button(this).apply {
            text = getString(R.string.enable_accessibility_service)
            setOnClickListener {
                // Simplified: Just open the accessibility menu.
                // Tell user to look for NannyGodmin in Downloaded/Installed Apps.
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                Toast.makeText(this@ConfigActivity, "Look for NannyGodmin under 'Downloaded apps' or 'Installed services'", Toast.LENGTH_LONG).show()
            }
        }

        layout.addView(btnEnableAdmin)
        layout.addView(btnUsageStats)
        layout.addView(btnOverlay)
        layout.addView(btnAccessibility)
    }

    private fun updateUI(url: String, clientId: String) {
        val isProvisioned = clientId.isNotEmpty()
        
        // Update Status Label
        if (isProvisioned) {
            statusWarning.text = getString(R.string.device_provisioned)
            statusWarning.setTextColor(Color.GREEN)
        } else {
            statusWarning.text = getString(R.string.device_not_provisioned_yet)
            statusWarning.setTextColor(Color.RED)
        }

        // Update URL Label
        val displayUrl = url.ifEmpty { getString(R.string.godmin_url_not_set) }
        urlStatus.text = getString(R.string.current_godmin_url, displayUrl)

        // Update ClientID Label
        val displayId = clientId.ifEmpty { getString(R.string.godmin_url_not_set) }
        clientIdStatus.text = getString(R.string.client_id_status, displayId)

        // Update Buttons
        btnSaveUrl.isEnabled = !isProvisioned
        btnUnprovision.isEnabled = isProvisioned
    }

    private fun provisionDevice(newUrl: String) {
        val deviceName = Settings.Global.getString(contentResolver, "device_name")
            ?: Settings.Global.getString(contentResolver, Settings.Global.DEVICE_NAME)
            ?: Build.MODEL
        val androidId = Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID)
            
        Log.i(TAG, "Attempting to provision device as: $deviceName ($androidId) at $newUrl")

        thread {
            try {
                val url = if (newUrl.endsWith("/")) "${newUrl}provision" else "$newUrl/provision"
                val connection = URL(url).openConnection() as HttpURLConnection
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.connectTimeout = 5000
                connection.readTimeout = 5000
                
                val body = JSONObject().apply {
                    put("deviceName", deviceName)
                    put("androidId", androidId)
                }.toString()
                
                connection.outputStream.use { it.write(body.toByteArray()) }

                if (connection.responseCode == HttpURLConnection.HTTP_OK) {
                    val response = connection.inputStream.bufferedReader().use { it.readText() }
                    val json = JSONObject(response)
                    val clientId = json.optString("clientId")
                    
                    runOnUiThread {
                        if (clientId.isNotEmpty()) {
                            Log.i(TAG, "Provisioning success. Received Client ID: $clientId")
                            saveProvisioning(newUrl, clientId, json.optInt("poll_interval_secs", 10))
                        } else {
                            handleProvisioningFailure(getString(R.string.no_client_id_response))
                        }
                    }
                } else {
                    runOnUiThread {
                        handleProvisioningFailure(getString(R.string.server_returned_error, connection.responseCode))
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "Provisioning network error", e)
                runOnUiThread {
                    handleProvisioningFailure(e.message ?: getString(R.string.unknown_error))
                }
            }
        }
    }

    private fun unprovisionDevice() {
        Log.i(TAG, "Manual unprovisioning requested")
        handleProvisioningFailure("Manual unprovision")
    }

    private fun saveProvisioning(url: String, clientId: String, pollIntervalSecs: Int) {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit(commit = true) {
            putString(KEY_SERVER_URL, url)
            putString(KEY_CLIENT_ID, clientId)
            putInt(KEY_POLL_INTERVAL, pollIntervalSecs)
        }
        
        updateUI(url, clientId)
        urlInput.setText(url)
        
        Toast.makeText(this, getString(R.string.url_saved, url), Toast.LENGTH_LONG).show()
        
        Log.i(TAG, "Provisioning saved, starting MainService")
        startForegroundService(Intent(this, MainService::class.java))
    }

    private fun handleProvisioningFailure(reason: String) {
        Log.w(TAG, "Provisioning failed or reset: $reason")
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit(commit = true) {
            remove(KEY_SERVER_URL)
            remove(KEY_CLIENT_ID)
            remove(KEY_POLL_INTERVAL)
        }
        
        stopService(Intent(this, MainService::class.java))
        
        updateUI("", "")
        
        if (reason != "Manual unprovision") {
            Toast.makeText(this, getString(R.string.provisioning_failed, reason), Toast.LENGTH_LONG).show()
        }
    }
}
