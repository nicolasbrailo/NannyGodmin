package com.nicobrailo.nannygodmin

import android.app.ActivityManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.util.Log
import android.view.Gravity
import android.view.KeyEvent
import android.view.WindowManager
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity

class LockActivity : AppCompatActivity() {

    companion object {
        const val ACTION_UNLOCK = "com.nicobrailo.nannygodmin.ACTION_UNLOCK"
        private const val TAG = "LockActivity"
    }

    private val unlockReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action == ACTION_UNLOCK) {
                Log.i(TAG, "NannyGodmin is unlocking the device")
                finish()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Show over the system lock screen
        setShowWhenLocked(true)
        
        // Ensure we don't wake the screen automatically
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setTurnScreenOn(false)
        }
        
        window.addFlags(
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
        )

        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(android.graphics.Color.BLACK)
        }

        val iconView = ImageView(this).apply {
            setImageResource(R.mipmap.ic_launcher)
            val size = (128 * resources.displayMetrics.density).toInt()
            layoutParams = LinearLayout.LayoutParams(size, size).apply {
                setMargins(0, 0, 0, (32 * resources.displayMetrics.density).toInt())
            }
        }

        val textView = TextView(this).apply {
            text = getString(R.string.device_locked)
            textSize = 32f
            setTextColor(android.graphics.Color.WHITE)
            gravity = Gravity.CENTER
        }

        layout.addView(iconView)
        layout.addView(textView)
        setContentView(layout)

        // Modern way to handle/disable back button
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                Log.i(TAG, "Back pressed - ignored")
            }
        })

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(unlockReceiver, IntentFilter(ACTION_UNLOCK), RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(unlockReceiver, IntentFilter(ACTION_UNLOCK))
        }
    }

    override fun onPause() {
        super.onPause()
        
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (pm.isInteractive) {
            Log.i(TAG, "Activity pausing while screen is ON, attempting to stay in front")
            try {
                val activityManager = getSystemService(ACTIVITY_SERVICE) as ActivityManager
                activityManager.moveTaskToFront(taskId, 0)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to move task to front", e)
            }
        } else {
            Log.d(TAG, "Activity pausing because screen turned OFF - ignoring countermeasures")
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        try {
            unregisterReceiver(unlockReceiver)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to unregister receiver", e)
        }
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        return when (keyCode) {
            KeyEvent.KEYCODE_HOME,
            KeyEvent.KEYCODE_APP_SWITCH,
            KeyEvent.KEYCODE_VOLUME_UP,
            KeyEvent.KEYCODE_VOLUME_DOWN -> true
            else -> super.onKeyDown(keyCode, event)
        }
    }
}
