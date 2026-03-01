package com.nicobrailo.nannygodmin

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            Log.i("BootReceiver", "Device booted, launching NannyGodmin")
            val serviceIntent = Intent(context, MainService::class.java)
            context.startForegroundService(serviceIntent)
        }
    }
}
