# PromptFit for iPhone — free personal installation

This is a separate native SwiftUI app. It uses Apple's free personal-device
signing, so TestFlight and a paid Apple Developer Program membership are not
required.

The iPhone is the interface; the existing PromptFit app on the Mac remains the
companion that creates FIT files and communicates with Garmin. Keep the Mac and
iPhone on the same trusted Wi-Fi while using the app.

## What is included

- Natural-language workout presets and an editable description.
- A dark-green workout graph as the primary review.
- Optional interpreted JSON in a secondary sheet.
- No automatic FIT download or Garmin upload.
- Explicit **Approve & queue** or **Modify description** choices.
- Manual **Save or share FIT** and **Upload this FIT to Garmin** actions.
- The current approved workout selected at the top of the queue.
- An immediate Garmin connection and upload-confirmation sheet.
- API keys stored in the iPhone Keychain.

## One-time installation

1. Double-click **Open PromptFit iPhone Project.command**. It opens the Mac App
   Store if full Xcode is not installed; otherwise it opens the app project.
   The smaller Command Line Tools package is not enough.
2. If you installed Xcode during step 1, double-click the command again. It
   opens the signed `PromptFit/PromptFit.xcodeproj` project used for your phone.
3. Connect the iPhone to the Mac with a cable, unlock it, and accept any
   **Trust This Computer** prompts.
4. In Xcode, select the blue **PromptFitIOS** project, then the **PromptFitIOS**
   target and **Signing & Capabilities**.
5. Enable **Automatically manage signing** and select **Add Account…** to sign
   in with your regular Apple Account. Choose the **Personal Team** bearing your
   name. No paid membership is needed.
6. At the top of Xcode, choose the connected iPhone as the run destination.
7. Press the triangular **Run** button. Follow any iPhone prompt to enable
   Developer Mode, then run once more if Xcode asks.

If Xcode says the bundle identifier is unavailable, change it under
**Signing & Capabilities** to something unique, such as
`com.yourname.promptfit`.

## Connect the app to the Mac

1. On the Mac, double-click `run_webapp.command` in the main PromptFit folder.
2. Leave its Terminal window open. It displays a phone address similar to:
   `http://MacBook-Pro.local:8000`
3. On the iPhone, open **PromptFit → Settings** and enter that entire address
   under **Mac companion**. This personal build prefills
   `http://MacBook-Pro.local:8000` automatically; only change it if the Mac's
   network name changes.
4. Tap **Test Mac connection** and allow local-network access when iOS asks.
5. Add the OpenAI or OpenRouter settings used to interpret workouts.

If the name-based address does not work, open **System Settings → Wi-Fi →
Details → TCP/IP** on the Mac and use its local IP address, for example
`http://192.168.1.25:8000`.

## Garmin setup

Garmin uses the saved connection on the Mac:

1. On the Mac, visit `http://localhost:8000/#garmin-connect`.
2. Connect Garmin there once and complete any verification request.
3. Return to **Settings** on the iPhone and tap **Check Garmin connection**.

The iPhone never uploads automatically. After graph review, approve the FIT and
tap the Garmin upload button. The confirmation sheet appears immediately.

## Everyday use

1. Start PromptFit on the Mac and leave it running.
2. Open PromptFit on the iPhone.
3. Enter a workout or choose a preset, then generate it for review.
4. Inspect the graph and choose **Approve & queue** or **Modify description**.
5. Save/share the FIT or upload the approved workout to Garmin.

The Mac is required only because FIT generation and the existing Garmin
integration currently run there. This avoids a paid hosted service and keeps
the installation free.
