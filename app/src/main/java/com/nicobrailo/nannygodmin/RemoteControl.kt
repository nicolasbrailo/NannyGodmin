package com.nicobrailo.nannygodmin

import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
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
        if (isRunning) {
            sendReport(JSONObject().apply {
                put("action", if (isScreenOn) "screen_on" else "screen_off")
            })
        }
    }

    private fun sendReport(extraData: JSONObject) {
        thread {
            try {
                val baseUrl = if (settings.serverUrl.endsWith("/")) settings.serverUrl else "${settings.serverUrl}/"
                val url = URL("${baseUrl}device_report")
                val connection = url.openConnection() as HttpURLConnection
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.connectTimeout = 5000
                connection.readTimeout = 5000

                val body = JSONObject().apply {
                    put("clientId", settings.clientId)
                    val keys = extraData.keys()
                    while (keys.hasNext()) {
                        val key = keys.next()
                        put(key, extraData.get(key))
                    }
                }.toString()

                connection.outputStream.use { it.write(body.toByteArray()) }

                if (connection.responseCode == HttpURLConnection.HTTP_OK) {
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
                } else if (connection.responseCode == HttpURLConnection.HTTP_UNAUTHORIZED) {
                    Log.w("RemoteControl", "HTTP 401 unauthorized - Stopping Godmin, triggering provisioning flow.")
                    handler.post { onUnauthorized() }
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
                if (!currentActivity.contains("LockActivity")) {
                    startLockActivity()
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
            else -> Log.w("RemoteControl", "Unknown command: $name")
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
