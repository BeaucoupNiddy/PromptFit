import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: WorkoutStore
    @State private var selection = 0
    @State private var showWelcome = false

    var body: some View {
        TabView(selection: $selection) {
            BuildWorkoutView()
                .tabItem { Label("Build", systemImage: "figure.run") }
                .tag(0)

            QueueView()
                .tabItem { Label("Queue", systemImage: "checklist") }
                .badge(store.selectedQueueIDs.count)
                .tag(1)

            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }
                .tag(2)
        }
        .tint(AppTheme.forest)
        .onAppear {
            showWelcome = store.companionURLString.isEmpty
            if showWelcome { selection = 2 }
        }
        .sheet(item: $store.uploadReport) { report in
            UploadConfirmationView(report: report, connection: store.garminStatus)
                .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: $showWelcome) {
            WelcomeView {
                showWelcome = false
                selection = 2
            }
            .interactiveDismissDisabled()
        }
    }
}

private struct WelcomeView: View {
    let continueAction: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            Spacer()
            ZStack {
                Circle().fill(AppTheme.lime).frame(width: 78, height: 78)
                Image(systemName: "figure.run")
                    .font(.system(size: 34, weight: .bold))
                    .foregroundStyle(AppTheme.deepForest)
            }

            Text("PromptFit on iPhone")
                .font(.largeTitle.weight(.bold))
            Text("Build and review workouts on your phone. Your Mac creates the FIT file and keeps the Garmin connection ready.")
                .font(.title3)
                .foregroundStyle(.secondary)

            VStack(alignment: .leading, spacing: 14) {
                welcomeRow("1", "Start PromptFit on your Mac")
                welcomeRow("2", "Keep the Mac and iPhone on the same Wi‑Fi")
                welcomeRow("3", "Copy the phone address shown in the Mac window")
            }

            Spacer()

            Button("Set up the Mac connection", action: continueAction)
                .buttonStyle(PrimaryButtonStyle())
        }
        .padding(26)
        .background(AppTheme.cream.ignoresSafeArea())
    }

    private func welcomeRow(_ number: String, _ text: String) -> some View {
        HStack(spacing: 13) {
            Text(number)
                .font(.headline)
                .foregroundStyle(AppTheme.deepForest)
                .frame(width: 32, height: 32)
                .background(AppTheme.lime)
                .clipShape(Circle())
            Text(text).font(.headline)
        }
    }
}
