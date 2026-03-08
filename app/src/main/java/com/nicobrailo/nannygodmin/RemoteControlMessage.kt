package com.nicobrailo.nannygodmin

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.Handler
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.WindowManager
import android.widget.TextView
import androidx.core.app.NotificationCompat
import androidx.core.graphics.toColorInt

class RemoteControlMessage(private val context: Context, private val handler: Handler) {

    /**
     * Shows a message as both a system notification and an overlay.
     * @param message The text to display.
     * @param timeout The duration in seconds before the message is dismissed.
     */
    fun showMessage(message: String, timeout: Int) {
        val timeoutMs = timeout * 1000L
        showSystemNotification(message, timeoutMs)
        showOverlay(message, timeoutMs)
    }

    private fun showSystemNotification(message: String, timeoutMs: Long) {
        val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channelId = "RemoteAlerts"

        val channel = NotificationChannel(
            channelId,
            "Remote Alerts",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "Notifications from the NannyGodmin administrator"
        }
        notificationManager.createNotificationChannel(channel)

        val notificationId = System.currentTimeMillis().toInt()
        val notification = NotificationCompat.Builder(context, channelId)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("Message from Admin")
            .setContentText(message)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setAutoCancel(true)
            .build()

        notificationManager.notify(notificationId, notification)

        // Automatically dismiss the notification after timeout
        if (timeoutMs > 0) {
            handler.postDelayed({
                notificationManager.cancel(notificationId)
            }, timeoutMs)
        }
    }

    private fun showOverlay(message: String, timeoutMs: Long) {
        if (!Settings.canDrawOverlays(context)) {
            Log.w("RemoteControlMessage", "Cannot show overlay: Permission not granted in Settings")
            return
        }

        val windowManager = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val textView = TextView(context).apply {
            text = message
            setBackgroundColor("#EE333333".toColorInt())
            setTextColor(Color.WHITE)
            setPadding(64, 48, 64, 48)
            gravity = Gravity.CENTER
            textSize = 18f
            elevation = 10f
        }

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP
            y = 150 // Offset from top
        }

        try {
            windowManager.addView(textView, params)
            
            val removeViewRunnable = Runnable {
                try {
                    windowManager.removeView(textView)
                } catch (e: Exception) { /* already removed */ }
            }

            // Auto-remove the overlay after timeout
            if (timeoutMs > 0) {
                handler.postDelayed(removeViewRunnable, timeoutMs)
            }
            
            // Also remove on click and cancel the pending removal
            textView.setOnClickListener {
                handler.removeCallbacks(removeViewRunnable)
                removeViewRunnable.run()
            }
        } catch (e: Exception) {
            Log.e("RemoteControlMessage", "Error showing overlay: ${e.message}")
        }
    }
}
