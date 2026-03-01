package com.nicobrailo.nannygodmin

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
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

    private lateinit var urlStatus: TextView
    private lateinit var clientIdStatus: TextView
    private lateinit var urlInput: EditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(50, 50, 50, 50)
        }

        val notProvisioned = intent.getBooleanExtra("EXTRA_NOT_PROVISIONED", false)
        if (notProvisioned) {
            val warning = TextView(this).apply {
                text = getString(R.string.device_not_provisioned_yet)
                setTextColor(android.graphics.Color.RED)
                textSize = 20f
                setPadding(0, 0, 0, 20)
            }
            layout.addView(warning)
        }

        val prefs = getSharedPreferences("prefs", MODE_PRIVATE)
        val currentUrl = prefs.getString("server_url", "")
        val currentClientId = prefs.getString("client_id", "")

        urlStatus = TextView(this).apply {
            val displayUrl = if (currentUrl.isNullOrEmpty()) {
                getString(R.string.godmin_url_not_set)
            } else {
                currentUrl
            }
            text = getString(R.string.current_godmin_url, displayUrl)
            setPadding(0, 0, 0, 10)
        }
        layout.addView(urlStatus)

        clientIdStatus = TextView(this).apply {
            val displayId = if (currentClientId.isNullOrEmpty()) {
                getString(R.string.godmin_url_not_set)
            } else {
                currentClientId
            }
            text = getString(R.string.client_id_status, displayId)
            setPadding(0, 0, 0, 20)
        }
        layout.addView(clientIdStatus)

        urlInput = EditText(this).apply {
            setHint(R.string.godmin_url_set_hint)
            setText(currentUrl)
        }
        layout.addView(urlInput)

        val btnSaveUrl = Button(this).apply {
            text = getString(R.string.save_url)
            setOnClickListener {
                val newUrl = urlInput.text.toString()
                if (newUrl.isNotEmpty()) {
                    provisionDevice(newUrl)
                } else {
                    Toast.makeText(this@ConfigActivity, R.string.please_enter_url, Toast.LENGTH_SHORT).show()
                }
            }
        }
        layout.addView(btnSaveUrl)

        // Handle Deep Link
        intent?.data?.let { uri ->
            if (uri.scheme == "nannygodmin" && uri.host == "config") {
                val newUrl = uri.getQueryParameter("url")
                if (newUrl != null) {
                    provisionDevice(newUrl)
                }
            }
        }

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

        layout.addView(btnEnableAdmin)
        layout.addView(btnUsageStats)
        layout.addView(btnOverlay)
        setContentView(layout)

        // Start the service if URL and ClientID are set
        if (!currentUrl.isNullOrEmpty() && !currentClientId.isNullOrEmpty()) {
            startForegroundService(Intent(this, MainService::class.java))
        }
    }

    private fun provisionDevice(newUrl: String) {
        // Fetch user-defined device name or fallback to model
        val deviceName = Settings.Global.getString(contentResolver, "device_name")
            ?: Settings.Global.getString(contentResolver, Settings.Global.DEVICE_NAME)
            ?: Build.MODEL
            
        Log.d("ConfigActivity", "Provisioning device as: $deviceName")

        thread {
            try {
                val url = if (newUrl.endsWith("/")) "${newUrl}provision" else "$newUrl/provision"
                val connection = URL(url).openConnection() as HttpURLConnection
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                
                val body = JSONObject().apply {
                    put("deviceName", deviceName)
                }.toString()
                
                connection.outputStream.use { it.write(body.toByteArray()) }

                if (connection.responseCode == HttpURLConnection.HTTP_OK) {
                    val response = connection.inputStream.bufferedReader().use { it.readText() }
                    val json = JSONObject(response)
                    val clientId = json.optString("clientId")
                    
                    runOnUiThread {
                        if (!clientId.isNullOrEmpty()) {
                            saveProvisioning(newUrl, clientId)
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
                runOnUiThread {
                    handleProvisioningFailure(e.message ?: getString(R.string.unknown_error))
                }
            }
        }
    }

    private fun saveProvisioning(url: String, clientId: String) {
        getSharedPreferences("prefs", MODE_PRIVATE).edit {
            putString("server_url", url)
            putString("client_id", clientId)
        }
        urlStatus.text = getString(R.string.current_godmin_url, url)
        clientIdStatus.text = getString(R.string.client_id_status, clientId)
        urlInput.setText(url)
        Toast.makeText(this, getString(R.string.url_saved, url), Toast.LENGTH_LONG).show()
        
        startForegroundService(Intent(this, MainService::class.java))
    }

    private fun handleProvisioningFailure(error: String) {
        getSharedPreferences("prefs", MODE_PRIVATE).edit {
            remove("server_url")
            remove("client_id")
        }
        urlStatus.text = getString(R.string.current_godmin_url, getString(R.string.godmin_url_not_set))
        clientIdStatus.text = getString(R.string.client_id_status, getString(R.string.godmin_url_not_set))
        Toast.makeText(this, getString(R.string.provisioning_failed, error), Toast.LENGTH_LONG).show()
    }
}
