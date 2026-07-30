import SwiftUI

struct QueueView: View {
    @EnvironmentObject private var store: WorkoutStore

    var body: some View {
        NavigationStack {
            Group {
                if store.isLoadingQueue && store.queue.isEmpty {
                    ProgressView("Loading workouts from your Mac…")
                } else if store.queue.isEmpty {
                    ContentUnavailableView(
                        "No approved workouts",
                        systemImage: "figure.run.circle",
                        description: Text("Approve a reviewed workout and it will appear here selected at the top.")
                    )
                } else {
                    List {
                        connectionSection

                        Section {
                            ForEach(store.orderedQueue) { file in
                                queueRow(file)
                            }
                        } header: {
                            HStack {
                                Text("Delivery queue")
                                Spacer()
                                Text("\(store.selectedQueueIDs.count) selected")
                            }
                        } footer: {
                            Text("Nothing uploads automatically. Only checked workouts are sent.")
                        }
                    }
                    .listStyle(.inset)
                    .safeAreaInset(edge: .bottom) {
                        uploadBar
                    }
                }
            }
            .background(AppTheme.cream)
            .navigationTitle("Workout queue")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task {
                            await store.loadQueue()
                            await store.refreshGarminStatus()
                        }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(store.isLoadingQueue || store.isCheckingConnection)
                }
            }
            .task {
                await store.loadQueue()
                await store.refreshGarminStatus()
            }
        }
        .sheet(item: $store.shareFile) { file in
            ShareSheet(items: [file.url])
        }
        .alert("PromptFit", isPresented: errorBinding) {
            Button("OK", role: .cancel) { store.errorMessage = nil }
        } message: {
            Text(store.errorMessage ?? "")
        }
    }

    private var connectionSection: some View {
        Section("Garmin Connect") {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: store.garminStatus.connected ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                    .font(.title2)
                    .foregroundStyle(store.garminStatus.connected ? AppTheme.success : .orange)
                VStack(alignment: .leading, spacing: 4) {
                    Text(connectionTitle)
                        .font(.headline)
                    Text(store.garminStatus.message)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.vertical, 4)
        }
    }

    private func queueRow(_ file: LocalFITFile) -> some View {
        HStack(spacing: 12) {
            Button {
                if store.selectedQueueIDs.contains(file.id) {
                    store.selectedQueueIDs.remove(file.id)
                } else {
                    store.selectedQueueIDs.insert(file.id)
                }
            } label: {
                Image(systemName: store.selectedQueueIDs.contains(file.id) ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
                    .foregroundStyle(store.selectedQueueIDs.contains(file.id) ? AppTheme.forest : .secondary)
            }
            .buttonStyle(.plain)

            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 7) {
                    Text(file.name)
                        .font(.headline)
                        .lineLimit(2)
                    if file.id == store.currentFITID {
                        Text("CURRENT")
                            .font(.caption2.weight(.heavy))
                            .foregroundStyle(AppTheme.deepForest)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 4)
                            .background(AppTheme.lime)
                            .clipShape(Capsule())
                    }
                }
                Text(fileMeta(file))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 4)

            Button {
                Task { await store.prepareShare(for: file) }
            } label: {
                Image(systemName: "square.and.arrow.down")
                    .font(.title3)
            }
            .buttonStyle(.plain)
            .foregroundStyle(AppTheme.forest)
            .accessibilityLabel("Save \(file.name)")
        }
        .padding(.vertical, 7)
    }

    private var uploadBar: some View {
        VStack(spacing: 10) {
            Divider()
            DatePicker(
                "Workout date",
                selection: $store.garminWorkoutDate,
                displayedComponents: .date
            )
            .datePickerStyle(.compact)
            .font(.subheadline.weight(.semibold))
            .tint(AppTheme.forest)
            .padding(.horizontal)

            Button {
                Task { await store.uploadSelected() }
            } label: {
                HStack {
                    if store.isUploading { ProgressView().tint(AppTheme.deepForest) }
                    Text(store.isUploading
                         ? "Waiting for Garmin…"
                         : "Upload \(store.selectedQueueIDs.count) selected")
                }
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(store.isUploading || store.selectedQueueIDs.isEmpty)
            .opacity(store.selectedQueueIDs.isEmpty ? 0.55 : 1)
            .padding(.horizontal)
            .padding(.bottom)
            .background(.regularMaterial)
        }
    }

    private var connectionTitle: String {
        if store.garminStatus.connected {
            if let name = store.garminStatus.accountName, !name.isEmpty { return "Connected as \(name)" }
            return "Garmin connected"
        }
        return "Garmin needs attention"
    }

    private func fileMeta(_ file: LocalFITFile) -> String {
        let kilobytes = max(1, Int((Double(file.size) / 1024).rounded()))
        if let modified = file.modified, !modified.isEmpty { return "\(modified) · \(kilobytes) KB" }
        return "\(kilobytes) KB"
    }

    private var errorBinding: Binding<Bool> {
        Binding(
            get: { store.errorMessage != nil },
            set: { if !$0 { store.errorMessage = nil } }
        )
    }
}
