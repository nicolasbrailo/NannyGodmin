package com.nicobrailo.nannygodmin

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

class RemoteControl(
    private val context: Context,
    private val settings: ConfigActivity.ProvisioningSettings,
    private val onUnauthorized: () -> Unit
) {
    private val handler = Handler(Looper.getMainLooper())
    private var isRunning = false
    private var isLocked = false
    private var currentActivity = "Unknown"

    private val pollTask = object : Runnable {
        override fun run() {
            if (!isRunning) return
            sendReport(JSONObject().apply {
                put("action", "poll")
            })
            handler.postDelayed(this, settings.pollIntervalSecs * 1000L)
        }
    }

    fun start() {
        if (isRunning) return
        isRunning = true
        handler.post(pollTask)
    }

    fun stop() {
        isRunning = false
        handler.removeCallbacks(pollTask)
    }

    /**
     * Specifically used when the device is no longer allowed to be managed.
     * Stops everything and forces an unlock.
     */
    fun stopAndUnlock() {
        stop()
        isLocked = false
        val unlockIntent = Intent(LockActivity.ACTION_UNLOCK).apply {
            setPackage(context.packageName)
        }
        context.sendBroadcast(unlockIntent)
    }

    fun onUserActivityChanged(oldActivity: String, newActivity: String) {
        this.currentActivity = newActivity
        
        // If not provisioned or paused (screen off), do nothing
        if (!isRunning) return

        sendReport(JSONObject().apply {
            put("action", "app_change")
            put("new_activity", newActivity)
            put("old_activity", oldActivity)
        })

        if (isLocked && !currentActivity.contains("LockActivity")) {
            startLockActivity()
        }
    }

    fun onScreenStateChanged(isScreenOn: Boolean) {
        // Screen events are always reported regardless of isRunning state
        sendReport(JSONObject().apply {
            put("action", if (isScreenOn) "screen_on" else "screen_off")
        })
    }

    private fun createConnection(path: String, contentType: String, timeoutMs: Int = 5000): HttpURLConnection {
        val baseUrl = if (settings.serverUrl.endsWith("/")) settings.serverUrl else "${settings.serverUrl}/"
        val url = URL("$baseUrl$path")
        return (url.openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            doOutput = true
            setRequestProperty("Content-Type", contentType)
            setRequestProperty("X-Client-Id", settings.clientId)
            connectTimeout = timeoutMs
            readTimeout = timeoutMs
        }
    }

    private fun sendReport(extraData: JSONObject) {
        thread {
            try {
                val connection = createConnection("device_report", "application/json")

                val body = JSONObject().apply {
                    put("clientId", settings.clientId)
                    val keys = extraData.keys()
                    while (keys.hasNext()) {
                        val key = keys.next()
                        put(key, extraData.get(key))
                    }
                }.toString()

                connection.outputStream.use { it.write(body.toByteArray()) }

                val responseCode = connection.responseCode
                if (responseCode == HttpURLConnection.HTTP_OK) {
                    val response = connection.inputStream.bufferedReader().use { it.readText() }
                    val json = JSONObject(response)
                    
                    val locked = json.optBoolean("locked", false)
                    val commands = json.optJSONArray("commands") ?: JSONArray()
                    
                    handler.post {
                        updateLockState(locked)
                        for (i in 0 until commands.length()) {
                            handleCommand(commands.getJSONObject(i))
                        }
                    }
                } else if (responseCode == HttpURLConnection.HTTP_NOT_FOUND || responseCode == HttpURLConnection.HTTP_UNAUTHORIZED) {
                    Log.w("RemoteControl", "Device unprovisioned (HTTP $responseCode). Stopping Godmin tasks.")
                    handler.post { onUnauthorized() }
                } else {
                    Log.e("RemoteControl", "Server returned unexpected status: $responseCode")
                }
            } catch (e: Exception) {
                Log.e("RemoteControl", "Error sending report: ${e.message}")
            }
        }
    }

    private fun updateLockState(locked: Boolean) {
        if (locked != isLocked) {
            isLocked = locked
            if (isLocked) {
                Log.d("RemoteControl", "Locking device (via server flag)")
                
                // 1. First ensure the LockActivity is starting or in front
                if (!currentActivity.contains("LockActivity")) {
                    startLockActivity()
                }

                // 2. Then physically turn off the screen
                val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
                val adminName = ComponentName(context, AdminReceiver::class.java)
                if (dpm.isAdminActive(adminName)) {
                    try {
                        Log.i("RemoteControl", "Requesting hardware lock/screen off")
                        dpm.lockNow()
                    } catch (e: SecurityException) {
                        Log.e("RemoteControl", "Failed to lockNow: ${e.message}")
                    }
                }
            } else {
                Log.d("RemoteControl", "Unlocking device (via server flag)")
                val unlockIntent = Intent(LockActivity.ACTION_UNLOCK).apply {
                    setPackage(context.packageName)
                }
                context.sendBroadcast(unlockIntent)
            }
        }
    }

    private fun handleCommand(command: JSONObject) {
        when (val name = command.optString("name")) {
            "set_volume" -> {
                val volume = command.optInt("arg", 50)
                setSystemVolume(volume)
            }
            "send_screenshot" -> {
                Log.d("RemoteControl", "Screenshot command received")
                takeAndSendScreenshot()
            }
            else -> Log.w("RemoteControl", "Unknown command: $name")
        }
    }

    private fun takeAndSendScreenshot() {
        NannyAccessibilityService.takeScreenshot(ContextCompat.getMainExecutor(context)) { bitmap ->
            if (bitmap != null) {
                uploadScreenshot(bitmap)
            } else {
                Log.e("RemoteControl", "Failed to take screenshot")
            }
        }
    }

    private fun uploadScreenshot(bitmap: Bitmap) {
        thread {
            try {
                val connection = createConnection("device_report/screenshot", "image/png", 15000)

                val stream = ByteArrayOutputStream()
                bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream)
                val byteArray = stream.toByteArray()

                connection.outputStream.use { it.write(byteArray) }

                val responseCode = connection.responseCode
                if (responseCode == HttpURLConnection.HTTP_OK) {
                    Log.d("RemoteControl", "Screenshot uploaded successfully")
                } else {
                    Log.e("RemoteControl", "Screenshot upload failed: $responseCode")
                }
            } catch (e: Exception) {
                Log.e("RemoteControl", "Error uploading screenshot: ${e.message}")
            } finally {
                bitmap.recycle()
            }
        }
    }

    private fun startLockActivity() {
        val intent = Intent(context, LockActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
        }
        context.startActivity(intent)
    }

    private fun setSystemVolume(volumePercent: Int) {
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val maxVolume = audioManager.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        val targetVolume = (maxVolume * (volumePercent / 100.0)).toInt()
        audioManager.setStreamVolume(AudioManager.STREAM_MUSIC, targetVolume, 0)
    }
}
