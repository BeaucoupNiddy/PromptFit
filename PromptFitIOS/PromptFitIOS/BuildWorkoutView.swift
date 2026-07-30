import SwiftUI

struct BuildWorkoutView: View {
    @EnvironmentObject private var store: WorkoutStore
    @State private var showJSON = false
    @FocusState private var promptFocused: Bool

    var body: some View {
        NavigationStack {
            ScrollViewReader { scrollProxy in
                ScrollView {
                    VStack(spacing: 18) {
                        builderCard
                            .id("builder")

                        WorkoutGraphView(graph: store.draft?.graph)
                            .id("graph")

                        if let draft = store.draft {
                            ReviewCard(draft: draft)
                        }

                        statusCard
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 30)
                }
                .background(AppTheme.cream.ignoresSafeArea())
                .navigationTitle("Build a workout")
                .onChange(of: store.draft?.id) { _, newValue in
                    guard newValue != nil else { return }
                    withAnimation { scrollProxy.scrollTo("graph", anchor: .top) }
                }
                .onChange(of: store.statusMessage) { _, _ in
                    if store.draft == nil && store.statusMessage.contains("Update") {
                        withAnimation { scrollProxy.scrollTo("builder", anchor: .top) }
                        promptFocused = true
                    }
                }
            }
        }
        .sheet(item: $store.shareFile) { file in
            ShareSheet(items: [file.url])
        }
        .sheet(isPresented: $showJSON) {
            JSONPreviewView(text: store.JSONPreview)
        }
        .alert("PromptFit", isPresented: errorBinding) {
            Button("OK", role: .cancel) { store.errorMessage = nil }
        } message: {
            Text(store.errorMessage ?? "")
        }
    }

    private var builderCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 5) {
                Text("ENTER YOUR OWN OR CHOOSE A PRESET")
                    .font(.caption.weight(.bold))
                    .tracking(1)
                    .foregroundStyle(AppTheme.muted)
                Text("Describe the session you want to run.")
                    .font(.title2.weight(.bold))
            }

            Picker("Workout preset", selection: Binding(
                get: { store.selectedPresetID },
                set: { store.applyPreset($0) }
            )) {
                Text("Choose a natural-language workout…").tag("")
                ForEach(WorkoutPreset.all) { preset in
                    Text(preset.title).tag(preset.id)
                }
            }
            .pickerStyle(.menu)
            .tint(AppTheme.forest)

            TextEditor(text: $store.prompt)
                .focused($promptFocused)
                .frame(minHeight: 150)
                .padding(10)
                .scrollContentBackground(.hidden)
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(Color.black.opacity(0.09))
                }
                .overlay(alignment: .topLeading) {
                    if store.prompt.isEmpty {
                        Text("Example: 15 min easy, then 6 × 3 min at 10K effort with 90 sec easy jog, finish with 10 min easy.")
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 15)
                            .padding(.vertical, 18)
                            .allowsHitTesting(false)
                    }
                }

            HStack(spacing: 12) {
                Picker("Race", selection: $store.raceDistance) {
                    ForEach(RaceDistance.allCases) { distance in
                        Text(distance.label).tag(distance)
                    }
                }
                .frame(maxWidth: .infinity)

                TextField("Race pace, e.g. 6:30", text: $store.referencePace)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.numbersAndPunctuation)
            }

            DisclosureGroup {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Add as many exact paces as you know. Entered paces take priority over estimates and stay saved on this iPhone.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)

                    ForEach(PaceAnchorKind.allCases) { anchor in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(anchor.label)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(AppTheme.muted)
                            TextField(anchor.placeholder, text: paceBinding(for: anchor))
                                .textFieldStyle(.roundedBorder)
                                .keyboardType(.numbersAndPunctuation)
                        }
                    }
                }
                .padding(.top, 10)
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Your pace profile")
                        .font(.headline)
                    Text("Optional · saved automatically")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .tint(AppTheme.forest)

            Toggle("Add pace targets", isOn: $store.targetsEnabled)
                .tint(AppTheme.forest)

            Button {
                promptFocused = false
                Task { await store.generate() }
            } label: {
                HStack {
                    if store.isGenerating { ProgressView().tint(AppTheme.deepForest) }
                    Text(store.isGenerating ? "Generating for review…" : "Generate FIT for review")
                }
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(!store.canGenerate)
            .opacity(store.canGenerate ? 1 : 0.52)

            Button {
                Task {
                    await store.previewInterpretedJSON()
                    if !store.JSONPreview.isEmpty { showJSON = true }
                }
            } label: {
                HStack {
                    if store.isPreviewingJSON { ProgressView() }
                    Text("Show interpreted JSON")
                }
            }
            .buttonStyle(SecondaryButtonStyle())
            .disabled(store.isPreviewingJSON || store.prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

            Text("The JSON view is optional. Your workout graph appears below for review.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .promptFitCard()
    }

    private var statusCard: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: store.errorMessage == nil ? "checkmark.shield" : "exclamationmark.triangle")
                .foregroundStyle(AppTheme.forest)
            Text(store.statusMessage)
                .font(.subheadline)
                .foregroundStyle(AppTheme.muted)
            Spacer(minLength: 0)
        }
        .promptFitCard()
    }

    private var errorBinding: Binding<Bool> {
        Binding(
            get: { store.errorMessage != nil },
            set: { if !$0 { store.errorMessage = nil } }
        )
    }

    private func paceBinding(for anchor: PaceAnchorKind) -> Binding<String> {
        Binding(
            get: { store.paceProfile[anchor.rawValue] ?? "" },
            set: { value in
                var updated = store.paceProfile
                updated[anchor.rawValue] = value
                store.paceProfile = updated
            }
        )
    }
}

private struct ReviewCard: View {
    @EnvironmentObject private var store: WorkoutStore
    let draft: WorkoutDraft

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("REVIEW DECISION")
                        .font(.caption.weight(.bold))
                        .tracking(1)
                        .foregroundStyle(AppTheme.muted)
                    Text(draft.filename)
                        .font(.headline)
                        .lineLimit(2)
                }
                Spacer()
                Text(draft.isApproved ? "APPROVED & SELECTED" : "AWAITING REVIEW")
                    .font(.caption2.weight(.heavy))
                    .padding(.horizontal, 9)
                    .padding(.vertical, 6)
                    .foregroundStyle(draft.isApproved ? AppTheme.deepForest : .orange)
                    .background((draft.isApproved ? AppTheme.lime : Color.orange).opacity(0.18))
                    .clipShape(Capsule())
            }

            Text(draft.isApproved
                 ? "This workout is selected at the top of your queue. Nothing leaves the app until you choose an action."
                 : "Review the workout preview above. Approve it, or change the description and generate another version.")
                .font(.subheadline)
                .foregroundStyle(AppTheme.muted)

            if draft.isApproved {
                VStack(alignment: .leading, spacing: 7) {
                    Text("WORKOUT DATE")
                        .font(.caption.weight(.bold))
                        .tracking(1)
                        .foregroundStyle(AppTheme.muted)
                    DatePicker(
                        "Schedule on Garmin",
                        selection: $store.garminWorkoutDate,
                        displayedComponents: .date
                    )
                    .datePickerStyle(.compact)
                    .tint(AppTheme.forest)
                }
                .padding(12)
                .background(AppTheme.forest.opacity(0.06))
                .clipShape(RoundedRectangle(cornerRadius: 13, style: .continuous))

                Button {
                    Task { await store.uploadCurrent() }
                } label: {
                    HStack {
                        if store.isUploading { ProgressView().tint(AppTheme.deepForest) }
                        Image(systemName: "arrow.up.circle.fill")
                        Text(store.isUploading ? "Waiting for Garmin…" : "Upload this FIT to Garmin")
                    }
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(store.isUploading)

                Button {
                    Task { await store.prepareShare() }
                } label: {
                    Label("Save or share FIT", systemImage: "square.and.arrow.up")
                }
                .buttonStyle(SecondaryButtonStyle())
            } else {
                Button {
                    Task { await store.approve() }
                } label: {
                    HStack {
                        if store.isApproving { ProgressView().tint(AppTheme.deepForest) }
                        Text(store.isApproving ? "Approving…" : "Approve & queue")
                    }
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(store.isApproving)

                Button("Modify description") { store.modifyDraft() }
                    .buttonStyle(SecondaryButtonStyle())
            }
        }
        .promptFitCard()
    }
}

private struct JSONPreviewView: View {
    @Environment(\.dismiss) private var dismiss
    let text: String

    var body: some View {
        NavigationStack {
            ScrollView {
                Text(text)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
            }
            .navigationTitle("Interpreted JSON")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
