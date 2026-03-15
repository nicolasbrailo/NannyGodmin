package com.nicobrailo.nannygodmin

import android.annotation.SuppressLint
import android.app.AppOpsManager
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.view.View
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
        val pollIntervalSecs: Int,
        val failOpen: Boolean
    )

    companion object {
        const val PREFS_NAME = "NannyGodminPrefs"
        const val KEY_SERVER_URL = "server_url"
        const val KEY_CLIENT_ID = "client_id"
        const val KEY_POLL_INTERVAL = "poll_interval_secs"
        const val KEY_FAIL_OPEN = "fail_open"
        const val EXTRA_FORCE_REPROVISION = "force_reprovision"
        const val EXTRA_UPDATE_URL = "update_url"
        const val TAG = "NannyGodmin"

        fun getSettings(context: Context): ProvisioningSettings? {
            val prefs = context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            val url = prefs.getString(KEY_SERVER_URL, null)
            val id = prefs.getString(KEY_CLIENT_ID, null)
            if (url == null || id == null) return null
            return ProvisioningSettings(
                url, id,
                prefs.getInt(KEY_POLL_INTERVAL, 10),
                prefs.getBoolean(KEY_FAIL_OPEN, false)
            )
        }

        fun clearClientId(context: Context) {
            context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit {
                remove(KEY_CLIENT_ID)
            }
        }

        fun updateConfig(context: Context, pollInterval: Int, failOpen: Boolean) {
            context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit {
                putInt(KEY_POLL_INTERVAL, pollInterval)
                putBoolean(KEY_FAIL_OPEN, failOpen)
            }
        }
    }

    private lateinit var statusWarning: TextView
    private lateinit var urlStatus: TextView
    private lateinit var clientIdStatus: TextView
    private lateinit var buildTimeView: TextView
    private lateinit var urlInput: EditText
    private lateinit var btnSaveUrl: Button
    private lateinit var btnUnprovision: Button
    private lateinit var btnUpdate: Button
    private lateinit var permissionContainer: LinearLayout

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
            clearClientId(this)
        }

        val settings = getSettings(this)
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

        buildTimeView = TextView(this).apply {
            textSize = 12f
            setTextColor(Color.GRAY)
            setPadding(0, 20, 0, 0)
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

        btnUpdate = Button(this).apply {
            text = getString(R.string.download_app_update)
            visibility = View.GONE
            setBackgroundColor(Color.BLUE)
            setTextColor(Color.WHITE)
        }

        permissionContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }

        layout.addView(permissionContainer)
        layout.addView(statusWarning)
        layout.addView(urlStatus)
        layout.addView(clientIdStatus)
        layout.addView(buildTimeView)
        layout.addView(urlInput)
        layout.addView(btnSaveUrl)
        layout.addView(btnUnprovision)
        layout.addView(btnUpdate)

        // Initial UI state setup
        updateUI(currentUrl, currentClientId)
        handleUpdateIntent(intent)

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

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleUpdateIntent(intent)
    }

    private fun handleUpdateIntent(intent: Intent?) {
        val updateUrl = intent?.getStringExtra(EXTRA_UPDATE_URL)
        if (!updateUrl.isNullOrEmpty()) {
            Log.i(TAG, "Update URL received: $updateUrl")
            btnUpdate.visibility = View.VISIBLE
            btnUpdate.setOnClickListener {
                try {
                    val browserIntent = Intent(Intent.ACTION_VIEW, updateUrl.toUri())
                    startActivity(browserIntent)
                } catch (_: Exception) {
                    Toast.makeText(this, getString(R.string.failed_to_open_update_url), Toast.LENGTH_SHORT).show()
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        refreshPermissionButtons()
    }

    private fun refreshPermissionButtons() {
        permissionContainer.removeAllViews()

        val dpm = getSystemService(DEVICE_POLICY_SERVICE) as DevicePolicyManager
        val adminName = ComponentName(this, AdminReceiver::class.java)

        // Device Admin
        if (!dpm.isAdminActive(adminName)) {
            val btnEnableAdmin = Button(this).apply {
                text = getString(R.string.enable_device_admin)
                setOnClickListener {
                    val intent = Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN).apply {
                        putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, adminName)
                        putExtra(DevicePolicyManager.EXTRA_ADD_EXPLANATION, getString(R.string.admin_explanation))
                    }
                    startActivity(intent)
                }
            }
            permissionContainer.addView(btnEnableAdmin)
        }

        // Usage Stats
        if (!isUsageStatsPermissionGranted()) {
            val btnUsageStats = Button(this).apply {
                text = getString(R.string.enable_usage_stats)
                setOnClickListener {
                    startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
                }
            }
            permissionContainer.addView(btnUsageStats)
        }

        // Overlay Permission
        if (!Settings.canDrawOverlays(this)) {
            val btnOverlay = Button(this).apply {
                text = getString(R.string.enable_overlay_permission)
                setOnClickListener {
                    val intent = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        "package:$packageName".toUri())
                    startActivity(intent)
                }
            }
            permissionContainer.addView(btnOverlay)
        }
    }

    private fun isUsageStatsPermissionGranted(): Boolean {
        val appOps = getSystemService(APP_OPS_SERVICE) as AppOpsManager
        val mode = appOps.checkOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS,
            android.os.Process.myUid(),
            packageName
        )
        return mode == AppOpsManager.MODE_ALLOWED
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

        // Update Build Time
        buildTimeView.text = getString(R.string.build_time, BuildConfig.BUILD_TIME)

        // Update Buttons
        btnSaveUrl.isEnabled = !isProvisioned
        btnUnprovision.isEnabled = isProvisioned
    }

    @SuppressLint("HardwareIds")
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
                            saveProvisioning(newUrl, clientId, json.optInt("poll_interval_secs", 10), json.optBoolean("fail_open", false))
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

    private fun saveProvisioning(url: String, clientId: String, pollIntervalSecs: Int, failOpen: Boolean) {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit(commit = true) {
            putString(KEY_SERVER_URL, url)
            putString(KEY_CLIENT_ID, clientId)
            putInt(KEY_POLL_INTERVAL, pollIntervalSecs)
            putBoolean(KEY_FAIL_OPEN, failOpen)
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
            remove(KEY_FAIL_OPEN)
        }
        
        stopService(Intent(this, MainService::class.java))
        
        updateUI("", "")
        
        if (reason != "Manual unprovision") {
            Toast.makeText(this, getString(R.string.provisioning_failed, reason), Toast.LENGTH_LONG).show()
        }
    }
}
