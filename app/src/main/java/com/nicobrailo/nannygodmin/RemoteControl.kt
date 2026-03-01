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
    private val serverUrl: String,
    private val clientId: String
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
            // TODO: When provisioning the device, retrieve settings like poll interval
            handler.postDelayed(this, 10000)
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

    fun onUserActivityChanged(oldActivity: String, newActivity: String) {
        this.currentActivity = newActivity
        
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
        sendReport(JSONObject().apply {
            put("action", if (isScreenOn) "screen_on" else "screen_off")
        })
    }

    private fun sendReport(extraData: JSONObject) {
        thread {
            try {
                val baseUrl = if (serverUrl.endsWith("/")) serverUrl else "$serverUrl/"
                val url = URL("${baseUrl}device_report")
                val connection = url.openConnection() as HttpURLConnection
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.connectTimeout = 5000
                connection.readTimeout = 5000

                val body = JSONObject().apply {
                    put("clientId", clientId)
                    // Merge extraData into the body
                    val keys = extraData.keys()
                    while (keys.hasNext()) {
                        val key = keys.next()
                        put(key, extraData.get(key))
                    }
                }.toString()

                connection.outputStream.use { it.write(body.toByteArray()) }

                if (connection.responseCode == HttpURLConnection.HTTP_OK) {
                    val response = connection.inputStream.bufferedReader().use { it.readText() }
                    val commands = JSONArray(response)
                    for (i in 0 until commands.length()) {
                        handleCommand(commands.getJSONObject(i))
                    }
                }
            } catch (e: Exception) {
                Log.e("RemoteControl", "Error sending report: ${e.message}")
            }
        }
    }

    private fun handleCommand(command: JSONObject) {
        val name = command.optString("name")
        handler.post {
            when (name) {
                "lock" -> {
                    Log.d("RemoteControl", "Service requests lock screen")
                    isLocked = true
                    if (!currentActivity.contains("LockActivity")) {
                        startLockActivity()
                    }
                }
                "unlock" -> {
                    Log.d("RemoteControl", "UNLOCK triggered")
                    isLocked = false
                    val unlockIntent = Intent(LockActivity.ACTION_UNLOCK).apply {
                        setPackage(context.packageName)
                    }
                    context.sendBroadcast(unlockIntent)
                }
                "set_volume" -> {
                    val volume = command.optInt("arg", 50)
                    Log.d("RemoteControl", "SET VOL REQD VOL= $volume")
                    setSystemVolume(volume)
                }
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
