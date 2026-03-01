package com.nicobrailo.nannygodmin

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat

class MainService : Service() {

    private lateinit var activityTracker: UserActivityTracker
    private lateinit var remoteControl: RemoteControl

    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                Intent.ACTION_SCREEN_OFF -> {
                    Log.i("MainService", "Screen OFF - NannyGodmin stopping")
                    if (::remoteControl.isInitialized) remoteControl.onScreenStateChanged(false)
                    if (::activityTracker.isInitialized) activityTracker.stop()
                    if (::remoteControl.isInitialized) remoteControl.stop()
                }
                Intent.ACTION_SCREEN_ON -> {
                    Log.i("MainService", "Screen ON - Enabling NannyGodmin")
                    if (::remoteControl.isInitialized) remoteControl.onScreenStateChanged(true)
                    if (::activityTracker.isInitialized) activityTracker.start()
                    if (::remoteControl.isInitialized) remoteControl.start()
                }
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (::remoteControl.isInitialized) {
            remoteControl.stop()
            activityTracker.stop()
        }
        initializeComponents()
        return START_STICKY
    }

    override fun onCreate() {
        super.onCreate()
        showNotification()
        initializeComponents()

        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_SCREEN_OFF)
        }
        registerReceiver(screenReceiver, filter)
    }

    private fun initializeComponents() {
        val prefs = getSharedPreferences("prefs", MODE_PRIVATE)
        val serverUrl = prefs.getString("server_url", null)
        val clientId = prefs.getString("client_id", null)
        
        if (serverUrl == null || clientId == null) {
            Log.i("MainService", "Device not provisioned, launching config")
            val configIntent = Intent(this, ConfigActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                putExtra("EXTRA_NOT_PROVISIONED", true)
            }
            startActivity(configIntent)
            return
        }

        Log.i("MainService", "NannyGodmin waking up, report URL: $serverUrl")

        remoteControl = RemoteControl(this, serverUrl, clientId)

        activityTracker = UserActivityTracker(this) { prevAct, newAct ->
            remoteControl.onUserActivityChanged(prevAct, newAct)
        }

        activityTracker.start()
        remoteControl.start()
    }

    private fun showNotification() {
        val kNotifChannelId = "MainServiceChannel"

        val serviceChannel = NotificationChannel(
            kNotifChannelId,
            "Main Service Channel",
            NotificationManager.IMPORTANCE_LOW
        )
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(serviceChannel)

        val notification = NotificationCompat.Builder(this, kNotifChannelId)
            .setContentTitle("NannyGodmin Service")
            .setContentText("Monitoring commands...")
            .setSmallIcon(android.R.drawable.ic_menu_info_details)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .build()

        val kNotificationId = 1
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(kNotificationId, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(kNotificationId, notification)
        }
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        Log.i("MainService", "User attempted to close NannyGodmin, restoring")
        val restartServiceIntent = Intent(applicationContext, this.javaClass)
        restartServiceIntent.setPackage(packageName)
        startForegroundService(restartServiceIntent)
        super.onTaskRemoved(rootIntent)
    }

    override fun onBind(intent: Intent?): IBinder? {
        return null
    }

    override fun onDestroy() {
        super.onDestroy()
        unregisterReceiver(screenReceiver)
        if (::activityTracker.isInitialized) activityTracker.stop()
        if (::remoteControl.isInitialized) remoteControl.stop()
    }
}
