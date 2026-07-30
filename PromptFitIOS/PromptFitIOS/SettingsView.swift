import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var store: WorkoutStore
    @State private var companionAddress = ""
    @State private var selectedProvider = "openai"
    @State private var selectedModel = ""
    @State private var apiKey = ""
    @State private var isTesting = false
    @State private var testSucceeded = false
    @State private var settingsSaved = false
    @FocusState private var focusedField: SettingsField?

    private enum SettingsField: Hashable {
        case companionAddress
        case model
        case apiKey
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://Your-Mac-Name.local:8000", text: $companionAddress)
                        .focused($focusedField, equals: .companionAddress)
                        .textContentType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.done)
                        .onSubmit { saveAndDismissKeyboard() }

                    Button {
                        Task {
                            saveSettings()
                            isTesting = true
                            testSucceeded = await store.testCompanion()
                            isTesting = false
                            if testSucceeded {
                                await store.refreshGarminStatus()
                                await store.loadQueue()
                            }
                        }
                    } label: {
                        HStack {
                            if isTesting { ProgressView() }
                            Label(testSucceeded ? "Mac connection confirmed" : "Test Mac connection",
                                  systemImage: testSucceeded ? "checkmark.circle.fill" : "network")
                        }
                    }
                    .disabled(isTesting || companionAddress.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                } header: {
                    Text("Mac companion")
                } footer: {
                    Text("Leave PromptFit running on the Mac and keep both devices on the same Wi‑Fi. Copy the phone address shown in the Mac window when PromptFit starts.")
                }

                Section {
                    Picker("Provider", selection: $selectedProvider) {
                        Text("OpenAI").tag("openai")
                        Text("OpenRouter").tag("openrouter")
                        Text("Choose automatically").tag("auto")
                    }
                    TextField("Model", text: $selectedModel)
                        .focused($focusedField, equals: .model)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.next)
                        .onSubmit { focusedField = .apiKey }
                    SecureField("API key", text: $apiKey)
                        .focused($focusedField, equals: .apiKey)
                        .textContentType(.password)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.done)
                        .onSubmit { saveAndDismissKeyboard() }
                } header: {
                    Text("Workout interpretation")
                } footer: {
                    Text("The key is stored in the iPhone Keychain. It is sent only to PromptFit on your Mac, which uses it to interpret your workout description.")
                }

                Section {
                    Button {
                        saveAndDismissKeyboard()
                    } label: {
                        HStack {
                            Spacer()
                            Label(settingsSaved ? "Settings saved" : "Save settings",
                                  systemImage: settingsSaved ? "checkmark.circle.fill" : "square.and.arrow.down")
                                .font(.headline)
                            Spacer()
                        }
                    }
                    .foregroundStyle(settingsSaved ? AppTheme.success : AppTheme.forest)
                }

                Section("Garmin Connect") {
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: store.garminStatus.connected ? "checkmark.circle.fill" : "exclamationmark.circle")
                            .font(.title2)
                            .foregroundStyle(store.garminStatus.connected ? AppTheme.success : .orange)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(garminTitle).font(.headline)
                            Text(store.garminStatus.message)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Button("Check Garmin connection") {
                        saveAndDismissKeyboard()
                        Task { await store.refreshGarminStatus() }
                    }
                    .disabled(store.isCheckingConnection || companionAddress.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }

                Section("Privacy and delivery") {
                    Label("FIT files never download automatically", systemImage: "hand.raised.fill")
                    Label("Approval is required before queueing", systemImage: "checkmark.seal.fill")
                    Label("Garmin receives only selected workouts", systemImage: "arrow.up.circle.fill")
                }

                Section {
                    Text("This free personal-device build is installed directly from Xcode. It does not use TestFlight or the App Store.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .scrollDismissesKeyboard(.interactively)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { saveAndDismissKeyboard() }
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { focusedField = nil }
                        .fontWeight(.semibold)
                }
            }
            .onAppear { loadSettings() }
            .onChange(of: companionAddress) { _, _ in settingsSaved = false }
            .onChange(of: selectedProvider) { _, _ in settingsSaved = false }
            .onChange(of: selectedModel) { _, _ in settingsSaved = false }
            .onChange(of: apiKey) { _, _ in settingsSaved = false }
        }
        .alert("PromptFit", isPresented: errorBinding) {
            Button("OK", role: .cancel) { store.errorMessage = nil }
        } message: {
            Text(store.errorMessage ?? "")
        }
    }

    private func loadSettings() {
        companionAddress = store.companionURLString
        selectedProvider = store.provider
        selectedModel = store.model
        apiKey = store.apiKey
    }

    private func saveSettings() {
        store.companionURLString = companionAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        store.provider = selectedProvider
        store.model = selectedModel.trimmingCharacters(in: .whitespacesAndNewlines)
        store.apiKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        store.statusMessage = "Settings saved securely on this iPhone."
        settingsSaved = true
    }

    private func saveAndDismissKeyboard() {
        saveSettings()
        focusedField = nil
    }

    private var garminTitle: String {
        guard store.garminStatus.connected else { return "Not connected" }
        if let name = store.garminStatus.accountName, !name.isEmpty { return "Connected as \(name)" }
        return "Connected"
    }

    private var errorBinding: Binding<Bool> {
        Binding(
            get: { store.errorMessage != nil },
            set: { if !$0 { store.errorMessage = nil } }
        )
    }
}
