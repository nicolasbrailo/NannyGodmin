package com.nicobrailo.nannygodmin

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Handler
import android.os.Looper

class UserActivityTracker(context: Context, private val onActivityChanged: (String, String) -> Unit) {
    private val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
    private val handler = Handler(Looper.getMainLooper())
    private var lastActivity = "Unknown"
    private var isRunning = false

    private val pollTask = object : Runnable {
        override fun run() {
            if (!isRunning) return
            
            val currentActivity = getForegroundActivityName()
            if (currentActivity != lastActivity) {
                onActivityChanged(lastActivity, currentActivity)
                lastActivity = currentActivity
            }
            
            handler.postDelayed(this, 2000)
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

    fun getCurrentActivity(): String = lastActivity

    private fun getForegroundActivityName(): String {
        var currentActivity = "Unknown"
        val time = System.currentTimeMillis()
        val usageEvents = usm.queryEvents(time - 1000 * 10, time)
        val event = UsageEvents.Event()
        while (usageEvents.hasNextEvent()) {
            usageEvents.getNextEvent(event)
            if (event.eventType == UsageEvents.Event.ACTIVITY_RESUMED) {
                currentActivity = "${event.packageName}/${event.className}"
            }
        }
        return currentActivity
    }
}
