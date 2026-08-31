package com.sundaybrief.opener;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Toast;

public class OpenActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        Uri data = getIntent().getData();
        String query = data != null ? data.getQueryParameter("q") : null;

        if (query != null) {
            ClipboardManager clipboard =
                (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            ClipData clip = ClipData.newPlainText("Gmail search", query);
            clipboard.setPrimaryClip(clip);
            Toast.makeText(this, "Search copied — paste into Gmail search", Toast.LENGTH_LONG).show();
        }

        PackageManager pm = getPackageManager();
        Intent launch = pm.getLaunchIntentForPackage("com.google.android.gm");
        if (launch != null) {
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(launch);
        }

        finish();
    }
}
